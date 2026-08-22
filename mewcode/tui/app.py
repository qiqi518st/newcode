"""TUI REPL 循环：状态机、prompt_toolkit 输入、Agent Event 消费、计时、ESC 中断、重试、Plan Mode、权限系统"""

import asyncio
import logging
import os
import time
from enum import Enum

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape

from ..agent import Agent
from ..agent.events import EventType, StopReason
from ..hooks.types import DispatchResult
from ..hooks.types import Event as HookEvent
from ..permission.hitl import HITLRequest, HITLResponse
from ..permission.modes import PermissionMode
from ..plans.manager import PlanManager, PlanMeta
from ..slash import CommandKind, CommandRegistry, parse_command
from ..slash.context import CommandContext

logger = logging.getLogger(__name__)


class SessionState(Enum):
    IDLE = "idle"  # 等待用户输入
    STREAMING = "streaming"  # 等待/接收模型流
    APPROVING = "approving"  # 等待用户确认权限
    RESUMING = "resuming"


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


def _create_key_bindings(on_shift_tab=None, on_tab=None) -> KeyBindings:
    """创建 key bindings：Alt+Enter 插入换行，Shift+Tab 切换权限模式，Tab 命令补全"""
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

    @kb.add("tab")
    def _(event):
        """Tab 命令补全：单匹配直接补全，多匹配弹列表（F9.6）"""
        if on_tab is not None:
            on_tab(event.current_buffer)

    return kb


class SlashCompleter(Completer):
    """注册表派生补全（F9）：/ 前缀匹配 name、排除 hidden、显示 name+description。

    只参与命令名前缀匹配（F9.2）；hidden 命令不参与（F9.5，由 registry.complete 保证）。
    """

    def __init__(self, repl: "REPL") -> None:
        self._repl = repl

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        registry = self._repl.command_registry
        if registry is None:
            return
        for cmd in registry.complete(text):
            yield Completion(
                cmd.name,
                start_position=-len(text),
                display=f"/{cmd.name}  {cmd.description}",
            )


class RichUIController:
    """实现 UIController（F6），包住 REPL 的 console / 模式 / token / 流式 / 退出 / 会话操作。

    命令 handler 经此接口操作界面，不直接触碰 REPL 内部（F6.2/F6.3）。
    show_message 一律 escape——命令输出视为纯文本，防止用户/计划内容里的 [] 被 Rich 当 markup 解析。
    """

    def __init__(self, repl: "REPL") -> None:
        self._repl = repl

    # ── ch12 Hook 分派（TUI 层 5 事件）─────────────────────
    async def _dispatch_hook(self, event: HookEvent, payload: dict) -> DispatchResult:
        """分派 Hook 事件；未接线（command_ctx.hooks 为 None）时短路空结果（N10）。"""
        ctx = getattr(self._repl, "command_ctx", None)
        engine = getattr(ctx, "hooks", None) if ctx else None
        if engine is None:
            return DispatchResult()
        return await engine.dispatch(event, payload)

    # ── 输出 ──────────────────────────────────────────────
    def show_message(self, text: str, style: str = "") -> None:
        self._repl._console.print(escape(text), style=style or None)

    # ── 用户消息注入（KindPrompt，F3.4）────────────────────
    async def send_user_message(self, text: str) -> None:
        repl = self._repl
        await repl._run_stream(text, "normal", "")
        repl.state = SessionState.IDLE

    # ── 按指定模式触发一轮 Agent（KindUI，/do /plan <task>）──
    async def run_agent(
        self,
        user_input: str,
        mode: str = "normal",
        plan_content: str = "",
        execute_slug: str = "",
    ) -> None:
        repl = self._repl
        if execute_slug:
            repl._executing_slug = execute_slug
        await repl._run_stream(user_input, mode, plan_content)
        if mode == "plan":
            await repl._confirm_pending_plan()
        repl.state = SessionState.IDLE

    # ── 权限模式 ───────────────────────────────────────────
    def get_permission_mode(self) -> str:
        return self._repl._permission_mode_label

    def set_permission_mode(self, mode: str) -> None:
        parsed = PermissionMode.parse(str(mode))
        if parsed is None:
            raise ValueError(f"未知权限模式: {mode}")
        repl = self._repl
        repl._permission_mode = parsed
        if repl.agent.permission is not None:
            repl.agent.permission.set_mode(parsed)

    # ── App 模式（plan / normal）────────────────────────────
    def get_app_mode(self) -> str:
        return self._repl.mode.value

    def set_app_mode(self, mode: str) -> None:
        repl = self._repl
        if mode not in ("plan", "normal"):
            raise ValueError(f"未知 App 模式: {mode}")
        repl.mode = AppMode.PLAN if mode == "plan" else AppMode.NORMAL

    # ── 查询 ───────────────────────────────────────────────
    def query_token_usage(self) -> tuple[int, int]:
        return self._repl._session_in_tokens, self._repl._session_out_tokens

    def query_tool_count(self) -> int:
        return self._repl.agent.registry.count()

    def query_memory_files(self) -> list[str]:
        mm = self._repl.memory_manager
        if mm is None:
            return []
        return [n.filename for n in mm.list_notes()]

    def get_model_name(self) -> str:
        return getattr(self._repl.agent.provider, "model", "") or ""

    def get_cwd(self) -> str:
        return os.getcwd()

    # ── Skill（ch11）───────────────────────────────────────
    def list_catalog_skills(self) -> list:
        """/skill list 数据源：从 command_ctx.catalog 取全部 Skill 摘要（未接线返回空）。"""
        ctx = getattr(self._repl, "command_ctx", None)
        catalog = getattr(ctx, "catalog", None) if ctx else None
        if catalog is None:
            return []
        try:
            from ..slash.ui import SkillSummary

            return [
                SkillSummary(
                    name=s.name,
                    description=s.meta.description,
                    source=s.source.value,
                )
                for s in catalog.list()
            ]
        except Exception:  # noqa: BLE001 - 展示层失败降级为空列表
            return []

    def list_active_skills(self) -> list:
        """当前激活 Skill 名（无 store 返回空）。"""
        store = getattr(self._repl.agent, "_active_skills", None)
        return store.names() if store is not None else []

    def clear_active_skills(self) -> None:
        """/clear 后清空激活 Skill（F5.5/F8.2）。"""
        try:
            self._repl.agent.clear_active_skills()
        except Exception:  # 清空失败不阻断 /clear（仅记录）
            logger.exception("clear_active_skills failed")

    def append_assistant_message(self, text: str) -> None:
        """fork 结果写回主对话：加入会话历史（后续轮可见）+ 打印（F3.1/N13）。"""
        try:
            self._repl.agent.conv.add_assistant(text)
        except Exception:  # 历史写入失败仅打印（仅记录）
            logger.exception("append_assistant_message history write failed")
        self._repl._console.print(escape(text))

    def add_token_usage(self, in_t: int, out_t: int) -> None:
        """fork token 写回主统计（N13：主对话可见独立执行开销）。"""
        self._repl._session_in_tokens += in_t
        self._repl._session_out_tokens += out_t

    # ── 生命周期 ───────────────────────────────────────────
    def request_exit(self) -> None:
        repl = self._repl
        repl._exit_requested = True
        repl.agent.cancel()  # 通知后台任务收 CancelledError（N12 最接近的等价实现）

    async def request_session_list(self) -> None:
        """/resume：打开历史会话列表，选中后恢复（沿用 ch09 约束）。"""
        repl = self._repl
        runtime, archive = repl.session_runtime, repl.session_archive
        if runtime is None or archive is None:
            self.show_message("当前未启用会话恢复", style="yellow")
            return
        sessions = archive.list()
        if not sessions:
            self.show_message("没有可恢复的会话", style="yellow")
            return
        repl.state = SessionState.RESUMING
        try:
            options = [
                (
                    s.session_id,
                    f"{s.title or '(无标题)'}  {s.session_id}  {s.model or ''}",
                )
                for s in sessions
            ]
            selected = await repl._ask_choice("选择会话：\n", options)
            if selected:
                await self.resume_session(selected)
                self.show_message(f"已恢复会话 {selected}", style="green")
        finally:
            repl.state = SessionState.IDLE

    async def resume_session(self, session_id: str) -> None:
        """/session_resume：恢复指定会话并把 agent/context 指向恢复后的会话。"""
        repl = self._repl
        runtime = repl.session_runtime
        if runtime is None:
            self.show_message("当前未启用会话恢复", style="yellow")
            return
        # ch12：session_end（旧会话）→ resume → 集中重置 → session_resume（F8.1/F2.2）
        await self._dispatch_hook(HookEvent.SESSION_END, {})
        conv = await runtime.resume(session_id)
        self._repoint(conv)
        await runtime.reset_for_new_session()
        await self._dispatch_hook(HookEvent.SESSION_RESUME, {})

    async def new_session(self) -> None:
        """/session_new：新建会话并把 agent/context 指向新会话。"""
        repl = self._repl
        runtime = repl.session_runtime
        if runtime is None:
            self.show_message("当前未启用会话管理", style="yellow")
            return
        # ch12：session_end（旧）→ create_new → 集中重置 → session_start（同 /clear）
        await self._dispatch_hook(HookEvent.SESSION_END, {})
        conv = runtime.create_new()
        self._repoint(conv)
        await runtime.reset_for_new_session()
        await self._dispatch_hook(HookEvent.SESSION_START, {})

    def _repoint(self, conv: object) -> None:
        """把 agent 与 context_mgr 指向新的 ConversationManager（旧引用留档不污染）。"""
        repl = self._repl
        repl.agent.conv = conv
        cm = getattr(repl.agent, "_context_mgr", None)
        if cm is not None:
            cm._conv = conv

    async def request_compact(self) -> None:
        """/compact：手动压缩（走 _handle_compact 的事件流与熔断菜单）。"""
        await self._repl._handle_compact()

    async def request_clear_session(self) -> None:
        """/clear：按 plan.md「/clear 原子重置顺序」执行。"""
        repl = self._repl
        runtime = repl.session_runtime
        if runtime is None:
            self.show_message("当前未启用会话持久化，无法 /clear", style="yellow")
            return
        # ch12：session_end（旧会话）→ create_new → 集中重置 → session_start（F8.1/F2.2）
        await self._dispatch_hook(HookEvent.SESSION_END, {})
        conv = (
            runtime.create_new()
        )  # close 旧 writer → 新会话上下文 → 新 writer → 重建 Conversation
        repl.agent.conv = conv
        cm = getattr(repl.agent, "_context_mgr", None)
        if cm is not None:
            cm.reset_for_new_session(
                conv
            )  # 清 L1 替换账本 + 自动闸 + 锚点 + 指向新会话（T0b）
        await runtime.reset_for_new_session()
        await self._dispatch_hook(HookEvent.SESSION_START, {})
        repl._session_in_tokens = 0
        repl._session_out_tokens = 0
        repl._current_turn = 0
        repl._executing_slug = ""
        repl.mode = AppMode.NORMAL

    # ── 交互选择 ───────────────────────────────────────────
    async def choose(
        self,
        question: str,
        options: list[tuple[str, str]],
        default_index: int = 0,
    ) -> str | None:
        return await self._repl._ask_choice(question, options, default_index)

    async def choose_multi(
        self, question: str, options: list[tuple[str, str]]
    ) -> list[str] | None:
        return await self._repl._ask_multi_choice(question, options)


class REPL:
    """prompt_toolkit REPL 循环"""

    def __init__(
        self,
        agent: Agent,
        renderer,  # RichRenderer
        plan_manager: PlanManager,
        default_mode: str = "normal",
        session_runtime=None,
        session_archive=None,
        memory_manager=None,
        command_registry: CommandRegistry | None = None,
        command_ctx: CommandContext | None = None,
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
        self.session_runtime = session_runtime
        self.session_archive = session_archive
        self.memory_manager = memory_manager

        # ch10：命令系统
        self.command_registry = command_registry
        self.command_ctx = command_ctx
        self.ui = RichUIController(
            self
        )  # UIController 实现（CommandContext.ui 注入用）
        self._exit_requested = False

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
            key_bindings=_create_key_bindings(
                self._cycle_permission_mode,
                self._handle_tab,
            ),
            style=_PROMPT_STYLE,
            completer=SlashCompleter(self),
            complete_while_typing=True,
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
        """底部状态栏：左侧权限模式，右侧 token 用量；中部高频命令提示从注册表派生（F11.2/N5）。"""
        pm = self._permission_mode_label
        tokens = f"Σ in:{self._session_in_tokens} out:{self._session_out_tokens}"
        reg = self.command_registry
        if reg is not None:
            names = [f"/{c.name}" for c in reg.list()[:6]]
            hint = " ".join(names) + " | /help 查看全部"
        else:
            hint = "/help"
        return f"{pm} | Shift+Tab 切换模式 | Alt+Enter 换行，Enter 发送 | {hint} | {tokens}"

    async def run(self) -> None:
        """主循环：回车输入 → dispatch_slash 分流（True=命令处理完）→ 否则走 AgentLoop。"""
        try:
            while True:
                if self._exit_requested:
                    self._console.print("再见！")
                    break
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

                # ch10：命令分流——/exit 经 dispatch 置 _exit_requested，返 True
                if await self.dispatch_slash(text):
                    if self._exit_requested:
                        self._console.print("再见！")
                        break
                    continue

                await self._process_input(text)
                if self._exit_requested:
                    self._console.print("再见！")
                    break

        finally:
            pass

    async def _process_input(self, text: str) -> None:
        """提交普通用户输入（所有 "/" 前缀输入已被 dispatch_slash 拦截），启动 Agent。"""
        # ch12：user_prompt_submit 可拦截（F7.5）——在消息写历史/启动 Agent 之前；
        # 拦截时输入框下方提示拒绝原因，消息不进入对话历史，焦点回输入框。
        hook_result = await self.ui._dispatch_hook(
            HookEvent.USER_PROMPT_SUBMIT, {"prompt": text}
        )
        if hook_result.blocked:
            self._console.print(
                escape(
                    f"[hook {hook_result.blocking_hook_name}] {hook_result.reason}"
                ),
                style="red",
            )
            return
        # 重置执行标记（/do 或确认弹窗时通过 run_agent(execute_slug) 重新设置）
        self._executing_slug = ""
        if self.mode == AppMode.PLAN:
            # 计划模式中，普通输入视为计划任务
            user_input, agent_mode, plan_content = text, "plan", ""
        else:
            self.mode = AppMode.NORMAL
            user_input, agent_mode, plan_content = text, "normal", ""

        await self._run_stream(user_input, agent_mode, plan_content)
        await self._confirm_pending_plan()
        # 不重置模式：plan 会话中执行/不执行后仍停留在 plan 模式
        self.state = SessionState.IDLE

    async def dispatch_slash(self, text: str) -> bool:
        """命令分流器：命中并处理完成返回 True；非命令返回 False（走 AgentLoop）。

        ch10 F2/AC2："/" 前缀输入一律在此处理；未命中/退化形态输出引导 /help（不拼 "/+name"，
        避免 `"未知命令: /, ..."` 悬空斜杠）；命令异常上屏不崩 REPL（F6 兜底）。
        """
        parsed = parse_command(text)
        if parsed is None:
            return False  # 空输入 / 非斜杠
        name, args = parsed
        cmd = self.command_registry.get(name) if name else None
        if cmd is None:
            self._console.print("未知命令。可用命令: 输入 /help 查看。", style="yellow")
            return True
        if args.strip() and not cmd.usage:
            # 未声明参数的命令携带参数 → 按未命中处理（F7.2）
            self._console.print("未知命令。可用命令: 输入 /help 查看。", style="yellow")
            return True
        # 状态机门：KindUI/KindPrompt 仅 idle（N3a/F4.1）
        if (
            cmd.kind in (CommandKind.UI, CommandKind.PROMPT)
            and self.state != SessionState.IDLE
        ):
            self._console.print("请等待当前任务完成", style="yellow")
            return True
        if self.command_ctx is None:
            self._console.print("命令系统未初始化", style="red")
            return True
        # ch12：command_execute 通知（F8.1，通知型不拦截）
        await self.ui._dispatch_hook(
            HookEvent.COMMAND_EXECUTE, {"command": name, "args": args}
        )
        try:
            await cmd.handler(self.command_ctx, args)
        except Exception as exc:  # noqa: BLE001 —— 命令实现出错对用户可见，不崩 REPL
            self._console.print(f"命令执行失败: {exc}", style="red")
        return True

    def _handle_tab(self, buffer) -> None:
        """Tab 命令补全（F9.6）：单匹配直接补全；多匹配用 complete_next 弹列表。"""
        if self.state != SessionState.IDLE:
            return
        text = buffer.text
        if not text.startswith("/"):
            return
        reg = self.command_registry
        if reg is None:
            return
        matches = reg.complete(text)
        if not matches:
            return
        if len(matches) == 1:
            cmd = matches[0]
            buffer.text = "/" + cmd.name + " "
            buffer.cursor_position = len(buffer.text)
            if cmd.arg_prompt:
                self._console.print(
                    f"  用法: {cmd.usage or cmd.arg_prompt}", style="dim"
                )
        else:
            # 多匹配：打开补全菜单（complete_while_typing 下已实时显示候选）
            buffer.complete_next()

    async def _confirm_pending_plan(self) -> None:
        """Plan 完成后确认（带 plan 文件信息，方向键选择）——从 _process_input 抽出，/plan 路径复用。"""
        if not self._pending_plan:
            return
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

    def _show_retry(self, attempt: int) -> None:
        self._console.print(f"重试 {attempt}/3…", style="bold yellow")

    def _show_error(self, err: Exception) -> None:
        self._console.print(f"错误: {escape(str(err))}", style="bold red")

    def _show_cancelled(self) -> None:
        self._console.print("已取消", style="yellow italic")

    def _show_done(self, elapsed: float) -> None:
        self._console.print(f"Done ({elapsed:.0f}s)", style="dim")
