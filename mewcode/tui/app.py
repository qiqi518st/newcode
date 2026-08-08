"""TUI REPL 循环：状态机、prompt_toolkit 输入、Agent Event 消费、计时、ESC 中断、重试"""

import asyncio
import time
from enum import Enum

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from ..agent import Agent
from ..agent.events import EventType
from .renderer import RichRenderer


class SessionState(Enum):
    IDLE = "idle"           # 等待用户输入
    STREAMING = "streaming"  # 等待/接收模型流


# 提示符风格
_PROMPT_STYLE = Style.from_dict({
    "prompt": "#00ff00 bold",
})


def _create_key_bindings() -> KeyBindings:
    """创建 key bindings：Alt+Enter 插入换行"""
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _(event):
        """Alt+Enter 插入换行"""
        event.current_buffer.insert_text("\n")

    return kb


class REPL:
    """prompt_toolkit REPL 循环"""

    def __init__(
        self,
        agent: Agent,
        renderer: RichRenderer,
    ) -> None:
        self.agent = agent
        self.renderer = renderer
        self.state = SessionState.IDLE
        self.cur_reply: str = ""
        self.turn_start: float = 0.0
        self._stream_task: asyncio.Task | None = None
        self._retry_count: int = 0
        self._console = Console()
        self._session = PromptSession(
            key_bindings=_create_key_bindings(),
            style=_PROMPT_STYLE,
            bottom_toolbar="Alt+Enter 换行，Enter 发送 | /exit 退出",
        )

    async def run(self) -> None:
        """主循环"""
        try:
            while True:
                try:
                    user_input = await self._session.prompt_async(
                        "❯ ",
                        bottom_toolbar="Alt+Enter 换行，Enter 发送 | /exit 退出",
                    )
                except KeyboardInterrupt:
                    if self.state == SessionState.STREAMING:
                        self._cancel_stream()
                        continue
                    # IDLE 状态下 Ctrl+C → 退出
                    self._console.print("再见！")
                    break
                except EOFError:
                    # Ctrl+D → 退出
                    self._console.print("再见！")
                    break

                text = user_input.strip()
                if not text:
                    continue

                if text in ("/exit", "/quit"):
                    self._console.print("再见！")
                    break

                await self._process_input(text)

        finally:
            # 清理终端状态
            pass

    async def _process_input(self, text: str) -> None:
        """提交用户输入，切换状态并开始消费 Agent Event"""
        self.state = SessionState.STREAMING
        self.cur_reply = ""
        self._retry_count = 0
        self.turn_start = time.monotonic()

        self._stream_task = asyncio.create_task(self._consume_agent_events(text))
        try:
            await self._stream_task
        except asyncio.CancelledError:
            self._show_cancelled()
        finally:
            self.state = SessionState.IDLE

    async def _consume_agent_events(self, user_input: str) -> None:
        """消费 Agent Event 流，Rich Live 渲染 Markdown + 工具行"""
        max_retries = 3
        retry_delay = 3.0

        for attempt in range(max_retries + 1):
            if attempt > 0:
                self._show_retry(attempt)
                await asyncio.sleep(retry_delay)

            buffer: str = ""
            error_occurred = False

            try:
                with Live(
                    Markdown(""),
                    console=self._console,
                    refresh_per_second=10,
                    vertical_overflow="visible",
                ) as live:
                    async for event in self.agent.run(user_input):
                        if event.type == EventType.TEXT:
                            text = event.payload
                            buffer += text
                            live.update(Markdown(buffer))
                        elif event.type == EventType.TOOL_CALL:
                            tc = event.payload
                            params = ", ".join(f"{k}={v!r}" for k, v in tc.arguments.items())
                            self._console.print(
                                f"● {tc.tool_name}({params})",
                                style="bold green",
                            )
                        elif event.type == EventType.TOOL_RESULT:
                            tr = event.payload
                            if tr.status == "error":
                                self._console.print(
                                    f"  ✗ {tr.error}",
                                    style="red",
                                )
                            else:
                                summary = tr.output[:200] + "..." if len(tr.output) > 200 else tr.output
                                self._console.print(f"  → {summary}", style="dim")
                        elif event.type == EventType.DONE:
                            self.cur_reply = buffer
                            elapsed = time.monotonic() - self.turn_start
                            self._show_done(elapsed)
                            return
                        elif event.type == EventType.ERROR:
                            error_occurred = True
                            self._show_error(event.payload)
                            break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                error_occurred = True
                self._show_error(e)

            if error_occurred and attempt < max_retries:
                self._retry_count = attempt + 1
                continue
            else:
                return

    def _cancel_stream(self) -> None:
        """ESC 取消当前流式 task"""
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()

    def _show_timer(self) -> str:
        """返回计时字符串"""
        elapsed = time.monotonic() - self.turn_start
        return f"Imagining… ({elapsed:.0f}s)"

    def _show_retry(self, attempt: int) -> None:
        """显示重试提示"""
        self._console.print(f"重试 {attempt}/3…", style="bold yellow")

    def _show_error(self, err: Exception) -> None:
        """显示错误信息（可区分样式）"""
        self._console.print(f"错误: {err}", style="bold red")

    def _show_cancelled(self) -> None:
        """显示取消提示"""
        self._console.print("已取消", style="yellow italic")

    def _show_done(self, elapsed: float) -> None:
        """显示本轮完成耗时"""
        self._console.print(f"Done ({elapsed:.0f}s)", style="dim")