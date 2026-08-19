"""TUI REPL 循环：状态机、prompt_toolkit 输入、Agent Event 消费、计时、ESC 中断、重试、Plan Mode、权限系统"""

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
from ..permission.hitl import HITLRequest, HITLResponse
from ..permission.modes import PermissionMode
from ..plans.manager import PlanManager, PlanMeta


class SessionState(Enum):
    IDLE = "idle"  # 等待用户输入
    STREAMING = "streaming"  # 等待/接收模型流
    APPROVING = "approving"  # 等待用户确认权限


class AppMode(Enum):
    NORMAL = "normal"
    PLAN = "plan"


# 提示符风格
_PROMPT_STYLE = Style.from_dict(
    {
        "prompt": "#ffffff bold",
    }
)

_CONTEXT_STYLE = "bold white"


def _create_key_bindings(on_shift_tab=None) -> KeyBindings:
    """创建 key bindings：Alt+Enter 插入换行，Shift+Tab 切换权限模式"""
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _(event):
        """Alt+Enter 插入换行"""
        event.current_buffer.insert_text("\n")

    @kb.add("s-tab")
    def _(event):
        """Shift+Tab 切换权限模式（回调到 REPL，仅 IDLE 态生效）"""
        if on_shift_tab is not None:
            on_shift_tab()

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

        # 权限系统（ch06）
        self._permission_mode: PermissionMode = PermissionMode.DEFAULT
        if agent.permission is not None:
            self._permission_mode = agent.permission.mode
        # HITL 状态
        self._pending_hitl: HITLRequest | None = None
        self._approve_cursor: int = 0

        self._session = PromptSession(
            key_bindings=_create_key_bindings(self._cycle_permission_mode),
            style=_PROMPT_STYLE,
        )
        # 交互选择（_ask_choice / _ask_multi_choice）使用独立 session
        self._choice_session = PromptSession(
            style=_PROMPT_STYLE,
        )

    @property
    def _mode_label(self) -> str:
        return "[plan]" if self.mode == AppMode.PLAN else "[normal]"

    @property
    def _permission_mode_label(self) -> str:
        """权限模式状态栏标签"""
        labels = {
            PermissionMode.DEFAULT: "DEFAULT",
            PermissionMode.ACCEPT_EDITS: "ACCEPT EDITS",
            PermissionMode.PLAN: "PLAN",
            PermissionMode.BYPASS: "BYPASS",
        }
        mode = getattr(self, "_permission_mode", PermissionMode.DEFAULT)
        return labels.get(mode, "DEFAULT")

    def _cycle_permission_mode(self) -> None:
        """Shift+Tab 循环切换四种权限模式（仅 IDLE 态生效）"""
        if self.state != SessionState.IDLE:
            return
        order = [
            PermissionMode.DEFAULT,
            PermissionMode.ACCEPT_EDITS,
            PermissionMode.PLAN,
            PermissionMode.BYPASS,
        ]
        current = getattr(self, "_permission_mode", PermissionMode.DEFAULT)
        try:
            idx = order.index(current)
        except ValueError:
            idx = 0
        nxt = order[(idx + 1) % len(order)]
        self._permission_mode = nxt
        if self.agent.permission is not None:
            self.agent.permission.set_mode(nxt)
        # scrollback 提示新模式
        self._console.print(f"权限模式: {self._permission_mode_label}", style="yellow")

    @property
    def _toolbar(self) -> str:
        """底部状态栏：左侧权限模式，右侧 token 用量"""
        pm = self._permission_mode_label
        tokens = f"Σ in:{self._session_in_tokens} out:{self._session_out_tokens}"
        return f"{pm} | Shift+Tab 切换模式 | Alt+Enter 换行，Enter 发送 | /plan /do /compact /delete-plan /normal /exit | {tokens}"

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
                    if self.state == SessionState.APPROVING:
                        self._cancel_approving()
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

                # ch08：/compact 手动压缩命令（不写 conversation，直接调 run_force_compact）
                if text in ("/compact", "/compact "):
                    await self._handle_compact()
                    continue

                await self._process_input(text)

        finally:
            pass

    async def _process_input(self, text: str) -> None:
        """提交用户输入，根据命令切换模式，启动 Agent"""
        if text.startswith("/") and not self._is_known_command(text):
            self._console.print(
                "未知命令。可用命令：/plan /do /compact /normal /exit",
                style="yellow",
            )
            return

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
            # plan 模式使用 plan 权限矩阵
            self._permission_mode = PermissionMode.PLAN
            if self.agent.permission is not None:
                self.agent.permission.set_mode(PermissionMode.PLAN)
            self._console.print(
                "已进入计划模式。请直接描述你的任务，例如：创建一个 hello.txt 文件。",
                style="bold cyan",
            )
            return
        elif text.startswith("/plan "):
            self.mode = AppMode.PLAN
            self._permission_mode = PermissionMode.PLAN
            if self.agent.permission is not None:
                self.agent.permission.set_mode(PermissionMode.PLAN)
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
                result = await self._select_plan_interactive()
                if result is None:
                    return
                plan_meta, plan_content = result
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

            if confirm and confirm != "not now" and meta is not None:
                # 用户选择执行：打印信息后执行（与 /do 路径一致）
                await self._run_plan_execution(meta, plan_buffer)

        # 不重置模式：plan 会话中执行/不执行后仍停留在 plan 模式
        self.state = SessionState.IDLE

    @staticmethod
    def _is_known_command(text: str) -> bool:
        """Return whether a slash command belongs to the REPL command surface."""
        command = text.split(maxsplit=1)[0]
        return command in {
            "/compact",
            "/delete-plan",
            "/do",
            "/exit",
            "/exit-plan",
            "/normal",
            "/plan",
            "/quit",
        }

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
        """统一执行计划入口：先打印 plan 信息，再以 execute 模式执行"""
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
                        elif event.type == EventType.HITL_REQUEST:
                            # 人在回路确认
                            self._pending_hitl = event.payload
                            self._approve_cursor = 0
                            # 暂停 Live 渲染
                            live.stop()
                            # 展示确认框
                            response = await self._show_hitl_confirm(event.payload)
                            # 恢复 Live
                            live.start()
                            live.update(Markdown(buffer))
                            self.agent.resolve_hitl(response)
                            self._pending_hitl = None
                        elif event.type == EventType.ERROR:
                            error_occurred = True
                            self._show_error(event.payload)
                            break
                        elif event.type == EventType.CONTEXT_COMPACTING:
                            # ch08 压缩中提示（F24a 自动 / F24b 紧急）
                            prefix = (
                                "上下文撞墙，自动压缩中..."
                                if event.payload == "force"
                                else "正在压缩上下文..."
                            )
                            prefix = "○ " + prefix
                            self._console.print(prefix, style=_CONTEXT_STYLE)
                        elif event.type == EventType.CONTEXT_OFFLOADED:
                            info = event.payload
                            self._console.print(
                                f"○ 大结果已落盘：{info.get('count', 0)} 个 "
                                f"（{info.get('spill_dir', '')}）",
                                style=_CONTEXT_STYLE,
                            )
                        elif event.type == EventType.CONTEXT_COMPACTED:
                            outcome = event.payload
                            saved = outcome.before_tokens - outcome.after_tokens
                            self._console.print(
                                f"○ 上下文压缩完成：token "
                                f"{outcome.before_tokens} -> {outcome.after_tokens} "
                                f"（节省 {saved}，落盘替换 "
                                f"{outcome.replaced_results} 个）",
                                style=_CONTEXT_STYLE,
                            )
                        elif event.type == EventType.COMPACT_FAILED:
                            # ch08 熔断收尾菜单（F28）：暂停 Live，弹选择菜单
                            reason = getattr(event.payload, "failure_reason", "未知")
                            self._console.print(
                                f"○ 上下文压缩失败：{reason}", style=_CONTEXT_STYLE
                            )
                            live.stop()
                            await self._show_compact_failed_menu(event.payload)
                            live.start()
                            live.update(Markdown(buffer))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 流式消费兜底，既有行为
                error_occurred = True
                self._show_error(e)

            if error_occurred and attempt < max_retries:
                self._retry_count = attempt + 1
                continue
            else:
                return

    async def _show_hitl_confirm(self, request: HITLRequest) -> HITLResponse:
        """展示人在回路确认框，返回用户选择"""
        self.state = SessionState.APPROVING

        options = [
            ("allow_once", "1. 允许本次"),
            ("allow_always", "2. 永久允许（写入本地配置）"),
            ("deny", "3. 拒绝本次"),
        ]

        question = (
            f"待批准: ● {request.tool_name}\n"
            f"  参数: {request.params_preview}\n"
            f"  原因: {request.reason}\n"
        )

        index = [0]

        def render_prompt() -> FormattedText:
            fragments: list[tuple[str, str]] = [
                ("bold", question),
            ]
            for i, (_value, label) in enumerate(options):
                marker = "▸ " if i == index[0] else "  "
                style = "bold cyan" if i == index[0] else "dim"
                fragments.append((style, f"{marker}{label}\n"))
            fragments.append(
                ("dim", "↑↓ 选择 · 回车确认 · Esc 取消 · 1/2/3 直选 · y=允许 n=拒绝")
            )
            return FormattedText(fragments)

        kb = KeyBindings()

        @kb.add("down", eager=True)
        @kb.add("j", eager=True)
        def _(event):
            index[0] = (index[0] + 1) % len(options)
            event.app.invalidate()

        @kb.add("up", eager=True)
        @kb.add("k", eager=True)
        def _(event):
            index[0] = (index[0] - 1) % len(options)
            event.app.invalidate()

        @kb.add("enter", eager=True)
        @kb.add("space", eager=True)
        def _(event):
            event.app.exit(result=options[index[0]][0])

        @kb.add("1", eager=True)
        def _(event):
            event.app.exit(result="allow_once")

        @kb.add("2", eager=True)
        def _(event):
            event.app.exit(result="allow_always")

        @kb.add("3", eager=True)
        def _(event):
            event.app.exit(result="deny")

        @kb.add("y", eager=True)
        def _(event):
            event.app.exit(result="allow_once")

        @kb.add("n", eager=True)
        @kb.add("d", eager=True)
        def _(event):
            event.app.exit(result="deny")

        @kb.add("c-c", eager=True)
        @kb.add("escape", eager=True)
        def _(event):
            event.app.exit(result=None)

        result = await self._choice_session.prompt_async(
            message=render_prompt,
            key_bindings=kb,
            bottom_toolbar=self._toolbar,
        )

        self.state = SessionState.STREAMING

        if result is None:
            return HITLResponse(action="deny")
        return HITLResponse(action=result)

    def _cancel_stream(self) -> None:
        """ESC/Ctrl+C 取消当前流式 task"""
        self.agent.cancel()
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()

    def _cancel_approving(self) -> None:
        """ESC/Ctrl+C 取消 HITL 等待"""
        self.agent.cancel()
        self._console.print("已取消", style="yellow italic")

    async def _handle_compact(self) -> None:
        """ch08：手动 /compact——调 run_force_compact，展示结果或弹熔断菜单（F24/F28 手动路径）。"""
        self._console.print("○ 正在手动压缩上下文...", style=_CONTEXT_STYLE)
        self.state = SessionState.STREAMING
        try:
            tool_defs = self.agent.registry.to_definitions()
            outcome = await self.agent.run_force_compact(tool_defs)
        except Exception as e:  # noqa: BLE001 — 压缩异常不崩 TUI
            self._show_error(e)
            self.state = SessionState.IDLE
            return
        if outcome is not None and getattr(outcome, "success", False):
            # F24：展示压缩前后 token 变化
            saved = outcome.before_tokens - outcome.after_tokens
            self._console.print(
                f"○ 上下文压缩完成：token "
                f"{outcome.before_tokens} -> {outcome.after_tokens} "
                f"（节省 {saved}，落盘替换 {outcome.replaced_results} 个）",
                style=_CONTEXT_STYLE,
            )
        else:
            # F28 手动路径：弹熔断菜单
            await self._show_compact_failed_menu(outcome)
        self.state = SessionState.IDLE

    async def _show_compact_failed_menu(self, outcome) -> None:
        """ch08 熔断收尾菜单（F28 三路径统一）：重试 / 分组丢弃重试 / 放弃 / 退出。

        当前为骨架：选中即视为放弃本次行动（菜单驱动的分组丢弃重试属新的压缩行动，
        后续按 MessageGroupDropper 步进再试；此处先提供选项与文案，执行兜底为放弃）。
        """
        reason = getattr(outcome, "failure_reason", "未知") if outcome else "未知"
        question = f"压缩失败（原因: {reason}）。\n请选择处置方式：\n"
        choice = await self._ask_choice(
            question,
            [
                ("retry", "重试本次压缩"),
                ("drop_retry", "分组丢弃重试（每次丢 2 组×3 次，再每次丢 20%）"),
                ("abort", "放弃本次压缩"),
                ("exit", "退出会话"),
            ],
            default_index=2,
        )
        if choice == "exit":
            self._console.print("再见！")
            raise SystemExit(0)
        elif choice == "retry":
            # 新的压缩行动（又有自己的 3 次重试，F28）
            await self._handle_compact()
        elif choice == "drop_retry":
            # 菜单分支分组丢弃重试：后续接 MessageGroupDropper 步进
            # 当前骨架先提示，按 plan 后续补全丢组循环
            self._console.print(
                "分组丢弃重试：该路径将按 user 分组逐步丢弃最旧组重试（F28 菜单分支）。",
                style="yellow",
            )
        else:
            self._console.print("已放弃本次压缩。", style="dim")

    async def _ask_choice(
        self,
        question: str,
        options: list[tuple[str, str]],
        default_index: int = 0,
    ) -> str | None:
        """在输入位置内联显示选项列表，↑/↓ 选择，Enter 确认。"""
        index = [default_index]

        def render_prompt() -> FormattedText:
            fragments: list[tuple[str, str]] = [
                ("bold", question),
                ("", "（↑/↓ 移动，Enter 确认，Esc 取消）\n"),
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
        """在输入位置内联显示多选列表，↑/↓ 移动、空格 勾选、Enter 确认。"""
        index = [0]
        selected: set[str] = set()

        def render_prompt() -> FormattedText:
            fragments: list[tuple[str, str]] = [
                ("bold", question),
                ("", "（↑/↓ 移动，空格 勾选/取消，Enter 确认，Esc 取消）\n"),
            ]
            for i, (value, label) in enumerate(options):
                marker = "▸ " if i == index[0] else "  "
                check = "◉ " if value in selected else "○ "
                style = "bold cyan" if i == index[0] else "dim"
                fragments.append((style, f"{marker}{check}{label}\n"))
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
        """打印 plan 文件信息"""
        status = "已执行" if meta.executed else "未执行"
        self._console.print(f"▶ {title}: {escape(meta.file)}", style="bold cyan")
        self._console.print(f"  创建时间: {escape(meta.created_at)}", style="dim")
        self._console.print(f"  状态: {status}", style="dim")
        self._console.print()

    async def _select_plan_interactive(self) -> tuple[PlanMeta, str] | None:
        """/do 无参时列出所有 plan，用内联方向键选择"""
        plans = self.plan_manager.list_plans()
        if not plans:
            self._console.print("没有已保存的计划。", style="yellow")
            return None

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
        """/delete-plan：内联多选删除 plan 文件"""
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

        confirm = await self._ask_choice(
            f"确认删除 {len(result)} 个计划？\n",
            [
                ("yes", "yes — 确认删除"),
                ("no", "no — 取消"),
            ],
            default_index=1,
        )
        if confirm != "yes":
            self._console.print("已取消", style="dim")
            return

        self.plan_manager.delete_plans(result)
        self._console.print(f"已删除 {len(result)} 个计划", style="bold green")

    def _show_retry(self, attempt: int) -> None:
        self._console.print(f"重试 {attempt}/3…", style="bold yellow")

    def _show_error(self, err: Exception) -> None:
        self._console.print(f"错误: {escape(str(err))}", style="bold red")

    def _show_cancelled(self) -> None:
        self._console.print("已取消", style="yellow italic")

    def _show_done(self, elapsed: float) -> None:
        self._console.print(f"Done ({elapsed:.0f}s)", style="dim")
