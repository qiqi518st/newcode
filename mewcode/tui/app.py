"""TUI REPL 循环：状态机、prompt_toolkit 输入、Agent Event 消费、计时、ESC 中断、重试、Plan Mode"""

import asyncio
import time
from enum import Enum

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape

from ..agent import Agent
from ..agent.events import EventType, StopReason
from ..plans.manager import PlanManager, PlanMeta


class SessionState(Enum):
    IDLE = "idle"  # 等待用户输入
    STREAMING = "streaming"  # 等待/接收模型流


class AppMode(Enum):
    NORMAL = "normal"
    PLAN = "plan"


# 提示符风格
_PROMPT_STYLE = Style.from_dict(
    {
        "prompt": "#00ff00 bold",
    }
)


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
        plan_manager: PlanManager,
        default_mode: str = "normal",
    ) -> None:
        self.agent = agent
        self.renderer = renderer
        self.state = SessionState.IDLE
        self.mode = AppMode.PLAN if default_mode == "plan" else AppMode.NORMAL
        self.plan_manager = plan_manager
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

        # plan mode 确认：存储 plan buffer + slug 供 _process_input 使用
        self._pending_plan: str = ""
        self._pending_slug: str = ""
        # 当前正在执行的 plan slug（execute 完成时标记已执行）
        self._executing_slug: str = ""

        self._session = PromptSession(
            key_bindings=_create_key_bindings(),
            style=_PROMPT_STYLE,
        )
        # 交互选择（_ask_choice / _ask_multi_choice）使用独立 session，
        # 避免其 key_bindings（Enter/ESC/↑↓）通过共享 session 污染主循环：
        # 曾出现主循环回车被弹窗的 Enter 绑定捕获，导致"回车又弹执行选项"。
        self._choice_session = PromptSession(
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
        return f"{mode} | Alt+Enter 换行，Enter 发送 | /plan /do /delete-plan /normal /exit | {tokens}"

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
        # 重置执行标记（/do 或确认弹窗时重新设置）
        self._executing_slug = ""

        # 无状态命令：/delete-plan 直接交互，不进流式
        if text == "/delete-plan":
            await self._delete_plan_interactive()
            return

        # 退出计划模式
        if text in ("/normal", "/exit-plan"):
            self.mode = AppMode.NORMAL
            self._console.print("已退出计划模式，回到普通模式。", style="bold cyan")
            return

        # 识别斜杠命令
        if text in ("/plan", "/plan "):
            # 无参数：进入计划模式但不启动 Agent，等用户描述任务
            self.mode = AppMode.PLAN
            self._console.print(
                "已进入计划模式（只读）。请直接描述你的任务，例如：创建一个 hello.txt 文件。",
                style="bold cyan",
            )
            return
        elif text.startswith("/plan "):
            self.mode = AppMode.PLAN
            user_input = text.removeprefix("/plan").strip()
            agent_mode = "plan"
            plan_content = ""
        elif text.startswith("/do"):
            # /do 不改变当前模式：plan 会话中执行后仍停留在 plan 模式
            slug_arg = text.removeprefix("/do").strip()
            if slug_arg:
                # /do <slug>：直接执行指定 plan
                plan_meta = self.plan_manager.get_plan(slug_arg)
                if plan_meta is None:
                    self._console.print(f"未找到计划: {slug_arg}", style="bold red")
                    return
                plan_content = self.plan_manager.read_plan_content(slug_arg)
                if not plan_content:
                    self._console.print(f"计划文件为空: {slug_arg}", style="bold red")
                    return
                await self._run_plan_execution(plan_meta, plan_content)
                return
            else:
                # /do（无参）：内联列出所有 plan 供选择
                plan_meta, plan_content = await self._select_plan_interactive()
                if plan_meta is None:
                    return
                await self._run_plan_execution(plan_meta, plan_content)
                return
        else:
            if self.mode == AppMode.PLAN:
                # 计划模式中，普通输入视为计划任务
                user_input = text
                agent_mode = "plan"
                plan_content = ""
            else:
                self.mode = AppMode.NORMAL
                user_input = text
                agent_mode = "normal"
                plan_content = ""

        await self._run_stream(user_input, agent_mode, plan_content)

        # Plan 完成后确认（带 plan 文件信息，方向键选择）
        if self._pending_plan:
            plan_buffer = self._pending_plan
            slug = self._pending_slug
            self._pending_plan = ""
            self._pending_slug = ""
            self.state = SessionState.IDLE

            # 查 plan 元数据组装信息
            meta = self.plan_manager.get_plan(slug)
            if meta:
                status = "已执行" if meta.executed else "未执行"
                info = (
                    f"计划已保存: plans/{meta.file}\n"
                    f"创建时间: {meta.created_at}\n"
                    f"状态: {status}\n"
                )
            else:
                info = f"计划已保存: plans/{slug}.md"

            confirm = await self._ask_choice(
                info,
                [
                    (f"/do {slug}", f"/do {slug} — 执行此计划"),
                    ("not now", "not now — 暂不执行，保留计划"),
                ],
                default_index=1,  # 默认选 not now，防误触
            )

            if confirm and confirm != "not now":
                # 用户选择执行：打印信息后执行（与 /do 路径一致）
                if meta is not None:
                    await self._run_plan_execution(meta, plan_buffer)

        # 不重置模式：plan 会话中执行/不执行后仍停留在 plan 模式
        self.state = SessionState.IDLE

    async def _run_stream(self, user_input: str, mode: str, plan_content: str) -> None:
        """启动并等待 Agent 流式执行"""
        self.state = SessionState.STREAMING
        self.cur_reply = ""
        self._retry_count = 0
        self.turn_start = time.monotonic()
        self._stream_task = asyncio.create_task(
            self._consume_agent_events(user_input, mode, plan_content)
        )
        try:
            await self._stream_task
        except asyncio.CancelledError:
            self._show_cancelled()

    async def _run_plan_execution(self, meta: PlanMeta, plan_content: str) -> None:
        """统一执行计划入口：先打印 plan 信息，再以 execute 模式执行

        所有执行计划的路径（/do <slug>、/do 选择、确认弹窗选执行）都走此方法，
        确保"执行时先打印 plan 文件信息"这一要求一致满足。
        """
        self._print_plan_info(meta)
        self._executing_slug = meta.slug
        await self._run_stream("", "execute", plan_content)

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
                                f"{k}={escape(repr(v))}"
                                for k, v in tc.arguments.items()
                            )
                            self._console.print(
                                f"● {tc.tool_name}({params})",
                                style="bold green",
                            )
                        elif event.type == EventType.TOOL_RESULT:
                            tr = event.payload
                            if tr.status == "error":
                                self._console.print(
                                    f"  ✗ {escape(tr.error)}",
                                    style="red",
                                )
                            else:
                                summary = (
                                    tr.output[:200] + "..."
                                    if len(tr.output) > 200
                                    else tr.output
                                )
                                self._console.print(
                                    f"  → {escape(summary)}", style="dim"
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
                                slug = self.plan_manager.create_plan("", buffer)
                                self._pending_plan = buffer
                                self._pending_slug = slug
                                self._console.print(
                                    f"计划已保存: plans/{slug}.md",
                                    style="bold green",
                                )

                            # Execute 模式自然完成后标记 plan 已执行
                            if (
                                mode == "execute"
                                and self._executing_slug
                                and stop_reason == StopReason.NATURAL
                            ):
                                self.plan_manager.mark_executed(self._executing_slug)
                                self._executing_slug = ""

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

    async def _ask_choice(
        self,
        question: str,
        options: list[tuple[str, str]],
        default_index: int = 0,
    ) -> str | None:
        """在输入位置内联显示选项列表，↑/↓ 选择，Enter 确认。

        返回选中的 value（options[i][0]）；Esc / Ctrl+C 取消返回 None。
        类似 Claude Code 提问时的操作逻辑，而非弹出独立对话框。
        """
        index = [default_index]

        def render_prompt() -> FormattedText:
            """动态渲染：问题 + 选项列表，当前选中项带 ▸ 标记"""
            fragments: list[tuple[str, str]] = [
                ("bold", question),
                ("", "\n"),
            ]
            for i, (_value, label) in enumerate(options):
                marker = "▸ " if i == index[0] else "  "
                style = "bold cyan" if i == index[0] else "dim"
                fragments.append((style, f"{marker}{label}\n"))
            return FormattedText(fragments)

        kb = KeyBindings()

        @kb.add("down", eager=True)
        def _(event):
            index[0] = (index[0] + 1) % len(options)
            event.app.invalidate()

        @kb.add("up", eager=True)
        def _(event):
            index[0] = (index[0] - 1) % len(options)
            event.app.invalidate()

        @kb.add("enter", eager=True)
        def _(event):
            event.app.exit(result=options[index[0]][0])

        @kb.add("c-c", eager=True)
        def _(event):
            event.app.exit(result=None)

        @kb.add("escape", eager=True)
        def _(event):
            event.app.exit(result=None)

        result = await self._choice_session.prompt_async(
            message=render_prompt,
            key_bindings=kb,
            bottom_toolbar=self._toolbar,
        )
        return result

    async def _ask_multi_choice(
        self,
        question: str,
        options: list[tuple[str, str]],
    ) -> list[str] | None:
        """在输入位置内联显示多选列表，↑/↓ 移动、空格 勾选、Enter 确认。

        返回选中的 value 列表；Esc / Ctrl+C 取消返回 None。
        类似 Claude Code 提问时的操作逻辑。
        """
        index = [0]
        selected: set[str] = set()

        def render_prompt() -> FormattedText:
            fragments: list[tuple[str, str]] = [
                ("bold", question),
                ("", "（↑/↓ 移动，空格 勾选/取消，Enter 确认，Esc 取消）\n"),
            ]
            displayed = 0
            for i, (value, label) in enumerate(options):
                marker = "▸ " if i == index[0] else "  "
                check = "◉ " if value in selected else "○ "
                style = "bold cyan" if i == index[0] else "dim"
                fragments.append((style, f"{marker}{check}{label}\n"))
                displayed += 1
            return FormattedText(fragments)

        kb = KeyBindings()

        @kb.add("down", eager=True)
        def _(event):
            index[0] = (index[0] + 1) % len(options)
            event.app.invalidate()

        @kb.add("up", eager=True)
        def _(event):
            index[0] = (index[0] - 1) % len(options)
            event.app.invalidate()

        @kb.add("space", eager=True)
        def _(event):
            value = options[index[0]][0]
            if value in selected:
                selected.discard(value)
            else:
                selected.add(value)
            event.app.invalidate()

        @kb.add("enter", eager=True)
        def _(event):
            event.app.exit(result=list(selected))

        @kb.add("c-c", eager=True)
        def _(event):
            event.app.exit(result=None)

        @kb.add("escape", eager=True)
        def _(event):
            event.app.exit(result=None)

        return await self._choice_session.prompt_async(
            message=render_prompt,
            key_bindings=kb,
            bottom_toolbar=self._toolbar,
        )

    def _print_plan_info(self, meta: PlanMeta, title: str = "执行计划") -> None:
        """打印 plan 文件信息：名称、创建时间、是否已执行

        所有执行计划的路径都先调用此方法打印信息，再启动执行。
        """
        status = "已执行" if meta.executed else "未执行"
        self._console.print(f"▶ {title}: {escape(meta.file)}", style="bold cyan")
        self._console.print(f"  创建时间: {escape(meta.created_at)}", style="dim")
        self._console.print(f"  状态: {status}", style="dim")
        self._console.print()

    async def _select_plan_interactive(self) -> tuple[PlanMeta, str] | None:
        """/do 无参时列出所有 plan，用内联方向键选择（类似 Claude Code 提问）"""
        plans = self.plan_manager.list_plans()
        if not plans:
            self._console.print("没有已保存的计划。", style="yellow")
            return None

        # 每个选项 value=slug，label 含序号/任务/状态/日期
        options: list[tuple[str, str]] = []
        for i, p in enumerate(plans, 1):
            status = "[已执行]" if p.executed else "[待执行]"
            label = f"{i}. {p.slug} — {p.task[:30]} {status} ({p.created_at[:10]})"
            options.append((p.slug, label))

        slug = await self._ask_choice(
            "选择要执行的计划：\n",
            options,
            default_index=0,
        )
        if slug is None:
            self._console.print("已取消", style="dim")
            return None

        meta = self.plan_manager.get_plan(slug)
        if meta is None:
            self._console.print(f"未找到计划: {slug}", style="bold red")
            return None
        plan_content = self.plan_manager.read_plan_content(slug)
        if not plan_content:
            self._console.print(f"计划文件为空: {slug}", style="bold red")
            return None
        return meta, plan_content

    async def _delete_plan_interactive(self) -> None:
        """/delete-plan：内联多选删除 plan 文件（↑/↓ 移动、空格 勾选）"""
        plans = self.plan_manager.list_plans()
        if not plans:
            self._console.print("没有可删除的计划。", style="yellow")
            return

        options: list[tuple[str, str]] = []
        for p in plans:
            status = "[已执行]" if p.executed else "[待执行]"
            label = f"{p.slug} — {p.task[:30]} {status} ({p.created_at[:10]})"
            options.append((p.slug, label))

        result = await self._ask_multi_choice("选择要删除的计划：\n", options)

        if result is None:
            self._console.print("已取消", style="dim")
            return
        if not result:
            self._console.print("未选中任何计划", style="yellow")
            return

        # 二次确认（单选 Y/N，内联）
        confirm = await self._ask_choice(
            f"确认删除 {len(result)} 个计划？\n",
            [
                ("yes", "yes — 确认删除"),
                ("no", "no — 取消"),
            ],
            default_index=1,  # 默认 no，防误删
        )
        if confirm != "yes":
            self._console.print("已取消", style="dim")
            return

        self.plan_manager.delete_plans(result)
        self._console.print(f"已删除 {len(result)} 个计划", style="bold green")

    def _show_retry(self, attempt: int) -> None:
        """显示重试提示"""
        self._console.print(f"重试 {attempt}/3…", style="bold yellow")

    def _show_error(self, err: Exception) -> None:
        """显示错误信息（转义，防止错误文本含 Rich 标记语法导致二次崩溃）"""
        self._console.print(f"错误: {escape(str(err))}", style="bold red")

    def _show_cancelled(self) -> None:
        """显示取消提示"""
        self._console.print("已取消", style="yellow italic")

    def _show_done(self, elapsed: float) -> None:
        """显示本轮完成耗时"""
        self._console.print(f"Done ({elapsed:.0f}s)", style="dim")
