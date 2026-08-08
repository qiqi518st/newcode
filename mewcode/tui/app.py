"""TUI REPL 循环：状态机、prompt_toolkit 输入、Agent Event 消费、计时、ESC 中断、重试、Plan Mode"""

import asyncio
import os
import time
from enum import Enum

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from ..agent import Agent
from ..agent.events import EventType, StopReason


class SessionState(Enum):
    IDLE = "idle"           # 等待用户输入
    STREAMING = "streaming"  # 等待/接收模型流


class AppMode(Enum):
    NORMAL = "normal"
    PLAN = "plan"


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
        renderer,  # RichRenderer
        plan_file: str = "plan.md",
        default_mode: str = "normal",
    ) -> None:
        self.agent = agent
        self.renderer = renderer
        self.state = SessionState.IDLE
        self.mode = AppMode.PLAN if default_mode == "plan" else AppMode.NORMAL
        self.plan_file = plan_file
        self.cur_reply: str = ""
        self.turn_start: float = 0.0
        self._stream_task: asyncio.Task | None = None
        self._retry_count: int = 0
        self._console = Console()

        # 累计 token 用量
        self._session_in_tokens: int = 0
        self._session_out_tokens: int = 0

        # 当前 turn 编号
        self._current_turn: int = 0

        # plan mode 确认：存储 plan buffer 供 _process_input 使用
        self._pending_plan: str = ""

        self._session = PromptSession(
            key_bindings=_create_key_bindings(),
            style=_PROMPT_STYLE,
        )

    @property
    def _mode_label(self) -> str:
        return "[plan]" if self.mode == AppMode.PLAN else "[normal]"

    @property
    def _toolbar(self) -> str:
        """底部状态栏"""
        mode = self._mode_label
        tokens = f"Σ in:{self._session_in_tokens} out:{self._session_out_tokens}"
        return f"{mode} | Alt+Enter 换行，Enter 发送 | /exit 退出 | {tokens}"

    async def run(self) -> None:
        """主循环"""
        try:
            while True:
                try:
                    user_input = await self._session.prompt_async(
                        "❯ ",
                        bottom_toolbar=self._toolbar,
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
            pass

    async def _process_input(self, text: str) -> None:
        """提交用户输入，根据命令切换模式，启动 Agent"""
        self.state = SessionState.STREAMING
        self.cur_reply = ""
        self._retry_count = 0
        self.turn_start = time.monotonic()

        # 识别斜杠命令
        if text.startswith("/plan"):
            self.mode = AppMode.PLAN
            user_input = text.removeprefix("/plan").strip() or "请描述你的需求，我会分析项目并生成计划。"
            agent_mode = "plan"
            plan_content = ""
        elif text == "/do":
            self.mode = AppMode.NORMAL
            user_input = ""
            agent_mode = "execute"
            plan_content = self._read_plan_file()
            if not plan_content:
                self._console.print(
                    f"计划文件 {self.plan_file} 不存在或为空",
                    style="bold red",
                )
                self.state = SessionState.IDLE
                return
        else:
            user_input = text
            agent_mode = "normal"
            plan_content = ""

        self._stream_task = asyncio.create_task(
            self._consume_agent_events(user_input, agent_mode, plan_content)
        )
        try:
            await self._stream_task
        except asyncio.CancelledError:
            self._show_cancelled()

        # Plan mode 完成后的确认
        if self._pending_plan:
            plan_buffer = self._pending_plan
            self._pending_plan = ""
            self.state = SessionState.IDLE
            self._console.print()
            confirm = await self._session.prompt_async(
                "是否执行此计划？[/do / not now] ",
                bottom_toolbar=self._toolbar,
            )
            confirm = confirm.strip().lower()
            if confirm in ("/do", "y", "yes", ""):
                self.mode = AppMode.NORMAL
                self.state = SessionState.STREAMING
                self.cur_reply = ""
                self._retry_count = 0
                self.turn_start = time.monotonic()
                self._stream_task = asyncio.create_task(
                    self._consume_agent_events("", "execute", plan_buffer)
                )
                try:
                    await self._stream_task
                except asyncio.CancelledError:
                    self._show_cancelled()

        self.state = SessionState.IDLE

    async def _consume_agent_events(
        self,
        user_input: str,
        mode: str,
        plan_content: str,
    ) -> None:
        """消费 Agent Event 流，Rich Live 渲染 Markdown + 工具行 + 状态更新"""
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
                    async for event in self.agent.run(
                        user_input, mode=mode, plan_content=plan_content
                    ):
                        if event.type == EventType.TEXT:
                            text = event.payload
                            buffer += text
                            live.update(Markdown(buffer))
                        elif event.type == EventType.TOOL_CALL:
                            tc = event.payload
                            params = ", ".join(
                                f"{k}={v!r}" for k, v in tc.arguments.items()
                            )
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
                                summary = (
                                    tr.output[:200] + "..."
                                    if len(tr.output) > 200
                                    else tr.output
                                )
                                self._console.print(
                                    f"  → {summary}", style="dim"
                                )
                        elif event.type == EventType.TOKEN_USAGE:
                            tu = event.payload
                            self._session_in_tokens += tu.input_tokens
                            self._session_out_tokens += tu.output_tokens
                        elif event.type == EventType.TURN_START:
                            self._current_turn = event.payload
                            elapsed = time.monotonic() - self.turn_start
                            self._console.print(
                                f"Turn {self._current_turn + 1}/10 · "
                                f"Imagining… ({elapsed:.0f}s)",
                                style="dim italic",
                            )
                        elif event.type == EventType.TURN_END:
                            te = event.payload
                            elapsed = time.monotonic() - self.turn_start
                            self._console.print(
                                f"Turn {te.turn + 1} done · "
                                f"{te.tool_call_count} tools · "
                                f"in:{te.token_usage.input_tokens} "
                                f"out:{te.token_usage.output_tokens} "
                                f"({elapsed:.0f}s)",
                                style="dim",
                            )
                        elif event.type == EventType.DONE:
                            stop_reason = event.payload
                            self.cur_reply = buffer
                            elapsed = time.monotonic() - self.turn_start

                            # 根据终止原因展示不同提示
                            if stop_reason == StopReason.NATURAL:
                                self._show_done(elapsed)
                            elif stop_reason == StopReason.MAX_TURNS:
                                self._console.print(
                                    "达到迭代上限，已停止",
                                    style="bold yellow",
                                )
                            elif stop_reason == StopReason.CANCELLED:
                                self._show_cancelled()
                            elif stop_reason == StopReason.CONSECUTIVE_UNKNOWN_TOOLS:
                                self._console.print(
                                    "连续未知工具调用，已停止",
                                    style="bold yellow",
                                )
                            elif stop_reason == StopReason.STREAM_ERROR:
                                # 错误已在 ERROR 事件中展示
                                pass

                            # Plan Mode 产出写入文件
                            if mode == "plan" and buffer:
                                self._write_plan_file(buffer)
                                self._pending_plan = buffer

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
        """ESC/Ctrl+C 取消当前流式 task"""
        self.agent.cancel()
        # 等待 task 自然结束（Agent 会产出 DONE(CANCELLED)）
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()

    def _read_plan_file(self) -> str:
        """读取计划文件内容"""
        try:
            plan_path = os.path.join(os.getcwd(), self.plan_file)
            if os.path.exists(plan_path):
                with open(plan_path, "r", encoding="utf-8") as f:
                    return f.read()
        except Exception:
            pass
        return ""

    def _write_plan_file(self, content: str) -> None:
        """写入计划文件"""
        try:
            plan_path = os.path.join(os.getcwd(), self.plan_file)
            os.makedirs(os.path.dirname(plan_path), exist_ok=True)
            with open(plan_path, "w", encoding="utf-8") as f:
                f.write(content)
            self._console.print(
                f"计划已写入 {self.plan_file}",
                style="bold green",
            )
        except Exception as e:
            self._console.print(
                f"写入计划文件失败: {e}",
                style="bold red",
            )

    def _show_retry(self, attempt: int) -> None:
        """显示重试提示"""
        self._console.print(f"重试 {attempt}/3…", style="bold yellow")

    def _show_error(self, err: Exception) -> None:
        """显示错误信息"""
        self._console.print(f"错误: {err}", style="bold red")

    def _show_cancelled(self) -> None:
        """显示取消提示"""
        self._console.print("已取消", style="yellow italic")

    def _show_done(self, elapsed: float) -> None:
        """显示本轮完成耗时"""
        self._console.print(f"Done ({elapsed:.0f}s)", style="dim")