"""Agent ReAct 循环引擎：ch06 五层权限系统集成 + ch08 上下文管理"""

import asyncio
import uuid
from collections.abc import AsyncIterator

from ..conversation.manager import ConversationManager
from ..hooks import DispatchResult, Engine
from ..hooks.types import Event as HookEvent
from ..llm import PromptTooLongError
from ..permission.checker import PermissionChecker, extract_target, friendly_name
from ..permission.hitl import HITLRequest, HITLResponse
from ..permission.types import Decision
from ..prompt.assembler import PayloadAssembler
from ..prompt.reminders import hook_notification, plan_mode_reminder
from ..prompt.resources import EXECUTE_DIRECTIVE
from ..prompt.skills_block import (
    render_active_skills_block,
    render_skills_catalog,
)
from ..provider.base import Provider, ToolCall, ToolDefinition, ToolResult
from ..skills.adapter import (
    active_to_prompt_entries,
    catalog_to_prompt_items,
)
from ..tools.cwd import resolve_path
from ..tools.registry import Registry
from .events import Event, EventType, StopReason, TokenUsage, TurnEnd
from .scheduler import ScheduledResult, ToolScheduler

# 内置常量：最大迭代轮数
_MAX_AGENT_TURNS: int = 10


class Agent:
    """ReAct 循环编排：while 迭代直到自然完成 / 上限 / 取消 / 连续未知工具 / 出错"""

    def __init__(
        self,
        provider: Provider,
        conversation: ConversationManager,
        registry: Registry,
        stable_prompt: str = "",
        env_segment: str = "",
        permission: PermissionChecker | None = None,
        is_interactive: bool = True,
        context_mgr: object | None = None,  # ch08: ContextManager | None
        file_tracker: object | None = None,  # ch08: FileTracker | None
        memory_manager: object | None = None,
        active_skills: object | None = None,  # ch11: ActiveSkills | None
        hooks: Engine
        | None = None,  # ch12: hooks.Engine | None（None 时全部短路，N10）
        runtime: object
        | None = None,  # ch12: SessionRuntime | None（取 pending_reminders）
        max_turns: int = 10,  # ch13: 最大迭代轮数（主 Agent 缺省保持 10，spec F2.1）
        dont_ask: bool = False,  # ch13: dontAsk 模式（子 Agent ASK→ALLOW 短路，F5.3）
        teammate: object
        | None = None,  # ch15: TeammateContext | None（队员邮箱注入，F11.1）
        allowed_tools: list[str] | None = None,  # ch15: Coordinator 收窄白名单（F14.3）
    ) -> None:
        self.provider = provider
        self.conv = conversation
        self.registry = registry
        self._stable_prompt = stable_prompt
        self._env_segment = env_segment
        self._assembler = PayloadAssembler()
        self._scheduler = ToolScheduler(registry)
        self._cancelled = asyncio.Event()
        # ch13 子 Agent 参数（主 Agent 缺省不变）
        self._max_turns = max_turns
        self._dont_ask = dont_ask
        # ch15：队员上下文（TeammateContext，每轮邮箱注入）与工具白名单（Coordinator 收窄）
        self._teammate = teammate
        self._allowed_tools = allowed_tools

        # ch08 上下文管理（可选；无时行为与 ch07 一致，N8 向后兼容）
        self._context_mgr = context_mgr
        self._file_tracker = file_tracker
        self._memory_manager = memory_manager

        # ch11 Skill 状态（可选；None 时行为与 ch10 一致，N10 向后兼容）
        self._active_skills = active_skills
        # ch12 Hook 状态（可选；None 时所有 hook 调用短路，行为与 ch11 一致，N10）
        self._hooks = hooks
        self._runtime = runtime  # SessionRuntime：take_reminders / append_reminders
        self._catalog: object | None = None  # with_catalog 注入（阶段一摘要素材）
        self._natural_rounds = 0
        self._memory_task: asyncio.Task | None = None
        self._run_lock = asyncio.Lock()  # 会话级互斥（F34），管 run 与手动入口
        self._context_events: list[tuple[str, object]] = []  # context 事件累积区

        # 权限系统
        self._permission = permission
        self._interactive = is_interactive
        self._hitl_event = asyncio.Event()
        self._hitl_response: HITLResponse | None = None

    @property
    def permission(self) -> PermissionChecker | None:
        return self._permission

    # ── ch11 Skill 集成（F4.1/F5.2/F5.5）──────────────────
    def with_catalog(self, catalog: object | None) -> None:
        """注入 Skill catalog（阶段一摘要素材）；None 时跳过 env 组装（N10 向后兼容）。"""
        self._catalog = catalog

    def activate_skill(self, name: str, body: str) -> None:
        """激活一个 Skill 到会话（转发 ActiveSkills.activate，F4.2.1）。"""
        if self._active_skills is not None:
            self._active_skills.activate(name, body)

    def clear_active_skills(self) -> None:
        """清空激活 Skill（/clear 与 /session_new 调用，F5.5/F8.2）。"""
        if self._active_skills is not None:
            self._active_skills.clear()

    # ── ch15 Coordinator 工具收窄（F14.3/TD-11）──────────
    def set_allowed_tools(self, allowed: list[str] | None) -> None:
        """设置工具白名单（None=不限制）。

        生效：run() 的 tool_defs 收窄到白名单；known_calls 硬过滤（不在白名单的
        已知工具直接产 error 结果、不执行）——只藏定义可被注入提示绕过，双保险（TD-11）。
        """
        self._allowed_tools = allowed

    def _compose_env_segment(self) -> str:
        """每轮组装 env：基础环境 + Available Skills 摘要段（F4.1）+ 激活 Skill 段（F5.2）。

        catalog/active_skills 任一缺失时对应段省略（N10 向后兼容，无 Skill 时行为与 ch10 一致）。
        """
        parts = [self._env_segment] if self._env_segment else []
        if self._catalog is not None:
            block = render_skills_catalog(catalog_to_prompt_items(self._catalog))
            if block:
                parts.append(block)
        if self._active_skills is not None:
            block = render_active_skills_block(
                active_to_prompt_entries(self._active_skills)
            )
            if block:
                parts.append(block)
        return "\n\n".join(parts)

    # ── ch12 Hook 集成（F8.1/F8.3）──────────────────────────
    async def _dispatch_hook(self, event: HookEvent, payload: dict) -> DispatchResult:
        """分派 Hook 事件并收集 prompt 注入（F8.1）。

        hooks=None → 空结果（N10 无侵入短路）；injected_prompts 经
        runtime.append_reminders() 写入待注入队列（runtime=None 时丢弃）。
        """
        if self._hooks is None:
            return DispatchResult()
        result = await self._hooks.dispatch(event, payload)
        if result.injected_prompts and self._runtime is not None:
            self._runtime.append_reminders(result.injected_prompts)
        return result

    def _last_user_message(self) -> str:
        """conversation 末尾的 user 消息（pre_send payload，F3.4）。"""
        for m in reversed(self.conv.get_context()):
            if getattr(m, "role", None) == "user":
                content = getattr(m, "content", "")
                return content if isinstance(content, str) else ""
        return ""

    def cancel(self) -> None:
        """设置取消信号，TUI 在 ESC/Ctrl+C 时调用"""
        self._cancelled.set()
        # HITL 兜底解阻塞
        if not self._hitl_event.is_set():
            self._hitl_event.set()

    def resolve_hitl(self, response: HITLResponse) -> None:
        """TUI 调用来回传用户选择"""
        self._hitl_response = response
        self._hitl_event.set()

    async def run(
        self,
        user_input: str,
        mode: str = "normal",
        plan_content: str = "",
        *,
        _inject: bool = True,  # ch13: run_to_completion 驱动时任务已在 conv 则跳过（F5.2）
    ) -> AsyncIterator[Event]:
        """ReAct 循环入口，对外吐出统一 Event 流

        mode: "normal" | "plan" | "execute"
        plan_content: /do 时注入的计划文件内容
        """
        await self._run_lock.acquire()  # F34 会话级互斥（防手动 /compact 穿插）
        try:
            # 重置取消信号
            self._cancelled.clear()
            run_id = uuid.uuid4().hex[:12]

            # ch15 TD-14：成员执行上下文注入（嵌套 spawn 拦截；ContextVar 任务本地）
            if self._teammate is not None:
                from .team_hook import set_current_teammate

                set_current_teammate(self._teammate)

            # 注入用户消息（_inject=False 时任务已在 conv——run_to_completion 驱动）
            if _inject:
                if mode == "execute" and plan_content:
                    directive = EXECUTE_DIRECTIVE.format(plan=plan_content)
                    self.conv.add_user(directive)
                else:
                    self.conv.add_user(user_input)

            # 选择工具集（plan 模式暴露全部工具，由 SystemPrompt 引导自觉只读）
            # ch15 TD-11：allowed_tools 非空时收窄到白名单（Coordinator 模式）
            if self._allowed_tools is not None:
                tool_defs = self.registry.definitions_filtered(self._allowed_tools)
            else:
                tool_defs = self.registry.to_definitions()

            # ch12 ① turn_start（F8.1）：一轮对话开始（用户消息已入历史）
            await self._dispatch_hook(HookEvent.TURN_START, {"prompt": user_input})

            # 未知工具连续计数
            _unknown_streak: int = 0

            # 紧急压缩标记（F26：一次迭代内只重试一次）
            _emergency_retried: bool = False

            # ── ReAct 循环 ──
            for turn in range(self._max_turns):
                # 每轮开始前检查取消
                if self._cancelled.is_set():
                    yield Event(EventType.DONE, StopReason.CANCELLED)
                    return

                yield Event(EventType.TURN_START, turn)

                # ch08：每轮组装前自动上下文管理（L1 全量 + L2 阈值检查）
                if self._context_mgr is not None:
                    self._context_events = []
                    # ch12 ③ pre_compact / post_compact（trigger=auto）
                    await self._dispatch_hook(
                        HookEvent.PRE_COMPACT, {"trigger": "auto"}
                    )
                    await self._context_mgr.manage_context(tool_defs)
                    await self._dispatch_hook(
                        HookEvent.POST_COMPACT, {"trigger": "auto"}
                    )
                    for context_event in self._drain_context_events():
                        yield context_event

                # 轮次级补充消息：plan 模式按轮注入（第 0/5 轮完整，其余精简，瞬时不持久）
                reminders = [plan_mode_reminder(turn)] if mode == "plan" else []
                # ch12 ⑤ hook prompt 注入：拼到 plan reminder 之后、本轮回消费即清空（F8.3/AC24）
                if self._runtime is not None:
                    hook_prompts = self._runtime.take_reminders()
                    if hook_prompts:
                        reminders.extend(hook_notification(p) for p in hook_prompts)
                # ch15 TD-3：raw reminder 通道（<team-update> 等不经 hook_notification 包装）
                if self._runtime is not None:
                    raw_prompts = self._runtime.take_raw_reminders()
                    if raw_prompts:
                        reminders.extend(raw_prompts)
                # ch15 F11.1：队员每轮读 mailbox → <incoming-messages> reminder（惰性导入避环）
                if self._teammate is not None:
                    from .team_mailbox import inject_incoming

                    reminders.extend(await inject_incoming(self, self._teammate))
                # ch12 ④ pre_send：消息发给 LLM 之前（payload 含对话末尾 user 消息）
                if self._hooks is not None:
                    await self._dispatch_hook(
                        HookEvent.PRE_SEND,
                        {
                            "prompt": user_input,
                            "last_user_message": self._last_user_message(),
                        },
                    )

                # 组装管线：稳定提示(段1) + 环境(段2, 含 Skill 摘要/激活段) + 历史 + reminders + tools
                payload = self._assembler.assemble(
                    self._stable_prompt,
                    self._compose_env_segment(),
                    self.conv.get_context(),
                    reminders,
                    tool_defs if tool_defs else None,
                )
                self._attach_request_trace(payload, user_input, turn, run_id)

                # ── 发起 LLM 请求 ──
                stream = self.provider.stream(payload)

                _buffer: str = ""
                _tool_calls: list[ToolCall] = []
                _token_usage: TokenUsage | None = None
                _stream_error: Exception | None = None

                async for se in stream:
                    if se.text:
                        _buffer += se.text
                        yield Event(EventType.TEXT, se.text)
                    elif se.tool_call:
                        _tool_calls.append(se.tool_call)
                    elif se.usage:
                        _token_usage = se.usage
                    elif se.done:
                        if se.usage:
                            _token_usage = se.usage
                        break
                    elif se.err:
                        _stream_error = se.err
                        break

                # 流式错误处理
                if _stream_error is not None:
                    # ch08 PTL 紧急压缩（F25/F26）：超窗时强制 L1+摘要重试一次
                    if (
                        isinstance(_stream_error, PromptTooLongError)
                        and self._context_mgr is not None
                        and not _emergency_retried
                    ):
                        _emergency_retried = True
                        # ch12 ③ 紧急压缩路径 pre/post_compact（trigger=emergency）
                        await self._dispatch_hook(
                            HookEvent.PRE_COMPACT, {"trigger": "emergency"}
                        )
                        outcome = await self._context_mgr.force_compact(tool_defs)
                        await self._dispatch_hook(
                            HookEvent.POST_COMPACT, {"trigger": "emergency"}
                        )
                        for context_event in self._drain_context_events():
                            yield context_event
                        if outcome.success:
                            # 用新历史重组 payload 重试本轮（不进下一 turn）
                            payload = self._assembler.assemble(
                                self._stable_prompt,
                                self._compose_env_segment(),
                                self.conv.get_context(),
                                reminders,
                                tool_defs if tool_defs else None,
                            )
                            self._attach_request_trace(
                                payload, user_input, turn, run_id
                            )
                            stream = self.provider.stream(payload)
                            _buffer = ""
                            _tool_calls = []
                            _token_usage = None
                            _stream_error = None
                            async for se in stream:
                                if se.text:
                                    _buffer += se.text
                                    yield Event(EventType.TEXT, se.text)
                                elif se.tool_call:
                                    _tool_calls.append(se.tool_call)
                                elif se.usage:
                                    _token_usage = se.usage
                                elif se.done:
                                    if se.usage:
                                        _token_usage = se.usage
                                    break
                                elif se.err:
                                    _stream_error = se.err
                                    break
                            if _stream_error is not None:
                                # ch12 error 事件（STREAM_ERROR 路径，F3.1）
                                await self._dispatch_hook(
                                    HookEvent.ERROR, {"error": str(_stream_error)}
                                )
                                yield Event(EventType.ERROR, _stream_error)
                                yield Event(EventType.DONE, StopReason.STREAM_ERROR)
                                return
                        else:
                            await self._dispatch_hook(
                                HookEvent.ERROR,
                                {"error": "紧急压缩失败，上下文不可恢复"},
                            )
                            yield Event(
                                EventType.ERROR,
                                PromptTooLongError("紧急压缩失败，上下文不可恢复"),
                            )
                            yield Event(EventType.DONE, StopReason.STREAM_ERROR)
                            return
                    else:
                        # ch12 error 事件（STREAM_ERROR 路径，F3.1）
                        await self._dispatch_hook(
                            HookEvent.ERROR, {"error": str(_stream_error)}
                        )
                        yield Event(EventType.ERROR, _stream_error)
                        yield Event(EventType.DONE, StopReason.STREAM_ERROR)
                        return

                # ch12 ⑦ post_receive：收到 LLM 响应之后（F3.1）
                if self._hooks is not None:
                    await self._dispatch_hook(
                        HookEvent.POST_RECEIVE, {"message": _buffer}
                    )

                # Token 用量
                if _token_usage:
                    yield Event(EventType.TOKEN_USAGE, _token_usage)
                    # ch08：主对话路径成功后更新锚点（摘要路径不调，防污染 F14）
                    if self._context_mgr is not None:
                        self._context_mgr.update_anchor(
                            _token_usage, len(self.conv.get_messages_ref())
                        )

                # 自然终止：无工具调用
                if not _tool_calls:
                    if _buffer:
                        self.conv.add_assistant(_buffer)
                    self._natural_rounds += 1
                    self._schedule_memory_update(user_input)
                    # ch12 ⑪ turn_end（仅 NATURAL/MAX_TURNS，F3.1）
                    await self._dispatch_hook(HookEvent.TURN_END, {"iter": turn + 1})
                    yield Event(EventType.DONE, StopReason.NATURAL)
                    return

                # 分类已知/未知工具
                known_calls: list[ToolCall] = []
                unknown_calls: list[ToolCall] = []

                for tc in _tool_calls:
                    tool = self.registry.get(tc.tool_name)
                    if tool is None:
                        unknown_calls.append(tc)
                    else:
                        known_calls.append(tc)

                # 未知工具：逐个产出错误结果
                for tc in unknown_calls:
                    tr = ToolResult(
                        status="error",
                        error=f"未知工具: {tc.tool_name}",
                    )
                    yield Event(EventType.TOOL_CALL, tc)
                    yield Event(EventType.TOOL_RESULT, tr)

                # 判断连续未知工具
                if unknown_calls and not known_calls:
                    _unknown_streak += 1
                else:
                    _unknown_streak = 0

                if _unknown_streak >= 2:
                    # 为最后一轮的未知工具写入历史
                    self.conv.add_assistant_with_tool_calls(_buffer, _tool_calls)
                    for tc in unknown_calls:
                        self.conv.add_tool_result(
                            tc,
                            ToolResult(
                                status="error",
                                error=f"未知工具: {tc.tool_name}",
                            ),
                        )
                    yield Event(EventType.DONE, StopReason.CONSECUTIVE_UNKNOWN_TOOLS)
                    return

                # 已知工具：写入 assistant 消息
                self.conv.add_assistant_with_tool_calls(_buffer, _tool_calls)

                # 先写入未知工具的结果到历史
                for tc in unknown_calls:
                    self.conv.add_tool_result(
                        tc,
                        ToolResult(
                            status="error",
                            error=f"未知工具: {tc.tool_name}",
                        ),
                    )

                # ch15 TD-11：allowed_tools 硬过滤——不在白名单的已知工具直接产 error
                # 结果、不执行（防注入提示绕过 Coordinator 收窄；N8 运行时不可解锁）
                if self._allowed_tools is not None:
                    filtered_calls: list[ToolCall] = []
                    for tc in known_calls:
                        if tc.tool_name in self._allowed_tools:
                            filtered_calls.append(tc)
                            continue
                        tr = ToolResult(
                            status="error",
                            error=f"工具不在当前白名单（Coordinator 模式）: {tc.tool_name}",
                        )
                        yield Event(EventType.TOOL_CALL, tc)
                        yield Event(EventType.TOOL_RESULT, tr)
                        self.conv.add_tool_result(tc, tr)
                    known_calls = filtered_calls

                # ── 权限检查（ch06）──
                # 对每个 known_call 做权限检查，分出 allowed_calls
                allowed_calls: list[ToolCall] = []
                permission_results: list[tuple[ToolCall, Decision, str]] = []

                for tc in known_calls:
                    # ch12 ⑧ pre_tool_use（Hook 拦截在权限检查之前，F7.6）
                    hook_result = await self._dispatch_hook(
                        HookEvent.PRE_TOOL_USE,
                        {"tool_name": tc.tool_name, "tool_input": tc.arguments},
                    )
                    if hook_result.blocked:
                        # F7.4：拦截 → 复用权限 Deny 路径（TOOL_CALL + TOOL_RESULT(error)），
                        # 跳过权限引擎与真实工具执行
                        tr = ToolResult(
                            status="error",
                            error=(
                                f"[hook {hook_result.blocking_hook_name}] "
                                f"{hook_result.reason}"
                            ),
                        )
                        yield Event(EventType.TOOL_CALL, tc)
                        yield Event(EventType.TOOL_RESULT, tr)
                        self.conv.add_tool_result(tc, tr)
                        permission_results.append(
                            (tc, Decision.DENY, hook_result.reason)
                        )
                        continue

                    if self._permission is not None:
                        is_read_only = self.registry.is_read_only(tc.tool_name)
                        result = self._permission.check(
                            tc, is_interactive=self._interactive, read_only=is_read_only
                        )
                    else:
                        # 无权限检查器 → 全部放行
                        result = None

                    if result is None or result.decision == Decision.ALLOW:
                        allowed_calls.append(tc)
                        permission_results.append((tc, Decision.ALLOW, ""))
                    elif result.decision == Decision.DENY:
                        # Deny：产 TOOL_CALL + TOOL_RESULT(error) 事件，写入历史，不执行
                        tr = ToolResult(status="error", error=result.reason)
                        yield Event(EventType.TOOL_CALL, tc)
                        yield Event(EventType.TOOL_RESULT, tr)
                        self.conv.add_tool_result(tc, tr)
                        # ch12 ⑨ 被权限 Deny 的也触发 post_tool_use（is_error=True，F3.1）
                        await self._dispatch_hook(
                            HookEvent.POST_TOOL_USE,
                            {
                                "tool_name": tc.tool_name,
                                "tool_input": tc.arguments,
                                "tool_result": result.reason,
                                "is_error": True,
                            },
                        )
                        permission_results.append((tc, Decision.DENY, result.reason))
                    elif result.decision == Decision.ASK and self._dont_ask:
                        # ch13 dontAsk（F5.3）：子 Agent 自动批准规则未命中的工具（ASK→ALLOW）
                        # 规则/黑名单/沙箱的 DENY 在前一分支已拦，不受影响
                        allowed_calls.append(tc)
                        permission_results.append((tc, Decision.ALLOW, ""))
                    elif result.decision == Decision.ASK:
                        if not self._interactive:
                            # 非交互：直接转为 DENY
                            tr = ToolResult(status="error", error=result.reason)
                            yield Event(EventType.TOOL_CALL, tc)
                            yield Event(EventType.TOOL_RESULT, tr)
                            self.conv.add_tool_result(tc, tr)
                            # ch12 ⑨ 非交互 ASK→DENY 同样触发 post_tool_use（is_error=True）
                            await self._dispatch_hook(
                                HookEvent.POST_TOOL_USE,
                                {
                                    "tool_name": tc.tool_name,
                                    "tool_input": tc.arguments,
                                    "tool_result": result.reason,
                                    "is_error": True,
                                },
                            )
                            permission_results.append(
                                (tc, Decision.DENY, result.reason)
                            )
                        else:
                            # 交互：发 HITL 事件阻塞等待
                            self._hitl_event.clear()
                            self._hitl_response = None
                            fn = friendly_name(tc.tool_name)
                            info = extract_target(tc)
                            params_preview = (
                                info.target if info.target else str(tc.arguments)
                            )
                            request = HITLRequest(
                                tool_name=fn,
                                params_preview=params_preview,
                                reason=result.reason,
                            )
                            # ch12 ⑨ permission_request：权限审批请求弹出时
                            await self._dispatch_hook(
                                HookEvent.PERMISSION_REQUEST,
                                {
                                    "tool_name": tc.tool_name,
                                    "tool_input": tc.arguments,
                                },
                            )
                            yield Event(EventType.HITL_REQUEST, request)

                            # 阻塞等待用户决策
                            await self._hitl_event.wait()
                            self._hitl_event.clear()
                            response = self._hitl_response

                            if response is None or response.action == "deny":
                                tr = ToolResult(
                                    status="error",
                                    error="用户拒绝"
                                    if response is None
                                    else "用户拒绝了此操作",
                                )
                                yield Event(EventType.TOOL_CALL, tc)
                                yield Event(EventType.TOOL_RESULT, tr)
                                self.conv.add_tool_result(tc, tr)
                                # ch12 ⑨ 用户拒绝也触发 post_tool_use（is_error=True）
                                await self._dispatch_hook(
                                    HookEvent.POST_TOOL_USE,
                                    {
                                        "tool_name": tc.tool_name,
                                        "tool_input": tc.arguments,
                                        "tool_result": tr.error,
                                        "is_error": True,
                                    },
                                )
                                permission_results.append(
                                    (tc, Decision.DENY, "用户拒绝")
                                )
                            else:
                                # allow_once / allow_always
                                if (
                                    response.action == "allow_always"
                                    and self._permission is not None
                                ):
                                    try:
                                        self._permission.persist_local_allow(tc)
                                    except OSError:
                                        pass  # 仅记不阻断
                                allowed_calls.append(tc)
                                permission_results.append((tc, Decision.ALLOW, ""))

                # 执行 allowed_calls
                if allowed_calls:
                    scheduled = await self._scheduler.schedule(allowed_calls)

                    # 按原始顺序产出 TOOL_CALL + TOOL_RESULT 事件
                    # allowed_calls 保持原序，与 denied 项交叉
                    allowed_idx = 0
                    allowed_results: dict[int, ScheduledResult] = {
                        idx: sr for idx, sr in enumerate(scheduled)
                    }

                    for tc in _tool_calls:
                        if self.registry.get(tc.tool_name) is not None:
                            # 判断此 tc 是否在 allowed_calls 中
                            is_allowed = any(tc is ac for ac in allowed_calls)
                            if is_allowed:
                                sr = allowed_results[allowed_idx]
                                yield Event(EventType.TOOL_CALL, tc)
                                yield Event(EventType.TOOL_RESULT, sr.result)
                                # ch08：文件追踪（read_file 成功时记录纯净字节，F19/F19a）
                                if (
                                    self._file_tracker is not None
                                    and tc.tool_name == "read_file"
                                    and sr.result.status == "ok"
                                ):
                                    # ch14 F7.2：按 ctx cwd 解析（主 Agent enter worktree 后
                                    # 相对路径追踪正确；防误记到进程 cwd）
                                    abs_path = resolve_path(
                                        tc.arguments.get("path", "")
                                    )
                                    await self._file_tracker.record(
                                        abs_path,
                                        _strip_truncation(sr.result.output),
                                    )
                                # 写入历史
                                self.conv.add_tool_result(tc, sr.result)
                                # ch12 ⑩ post_tool_use（工具拿到 result 之后，F3.1）
                                result_text = (
                                    sr.result.output
                                    if sr.result.status == "ok"
                                    else (sr.result.error or "")
                                )
                                await self._dispatch_hook(
                                    HookEvent.POST_TOOL_USE,
                                    {
                                        "tool_name": tc.tool_name,
                                        "tool_input": tc.arguments,
                                        "tool_result": result_text[:1000],
                                        "is_error": sr.result.status != "ok",
                                    },
                                )
                                # ch12 file_change：write/edit 成功后（F3.1）
                                if sr.result.status == "ok" and tc.tool_name in (
                                    "write_file",
                                    "edit_file",
                                ):
                                    path = tc.arguments.get("path")
                                    if isinstance(path, str) and path:
                                        await self._dispatch_hook(
                                            HookEvent.FILE_CHANGE, {"file_path": path}
                                        )
                                allowed_idx += 1
                            # denied 的已知工具已在上面处理
                        # unknown 已在上面处理

                # 轮次统计
                turn_end = TurnEnd(
                    turn=turn,
                    tool_call_count=len(_tool_calls),
                    token_usage=_token_usage or TokenUsage(0, 0),
                )
                yield Event(EventType.TURN_END, turn_end)

                # 每轮结束后检查取消
                if self._cancelled.is_set():
                    yield Event(EventType.DONE, StopReason.CANCELLED)
                    return

            # 达到迭代上限
            if _buffer:
                self.conv.add_assistant(_buffer)
            # ch12 ⑪ turn_end（MAX_TURNS 也触发，F3.1）
            await self._dispatch_hook(HookEvent.TURN_END, {"iter": self._max_turns})
            yield Event(EventType.DONE, StopReason.MAX_TURNS)
        finally:
            self._run_lock.release()

    async def run_to_completion(
        self,
        task: str,
        *,
        already_injected: bool = False,
        observer=None,
    ) -> str:
        """子 Agent「跑到底」（ch13 spec F5.2）：驱动 run() 复用主循环，返回最后一条文本。

        - already_injected=True：任务已在 conv（Fork 路径 build_forked_messages 已注入）→ 不重复注入
        - observer：可选事件回调（Manager 用它聚合 tool_count / usage，T12）
        - 终止：NATURAL → 返回文本；MAX_TURNS → 抛 MaxTurnsReached；CANCELLED →
          CancelledError；STREAM_ERROR / CONSECUTIVE_UNKNOWN_TOOLS → 抛 RuntimeError
        - 子 Agent 无 memory_manager/context_mgr/runtime → 主对话专属逻辑天然不触发
        """
        final_text = ""
        tool_count = 0
        usage = TokenUsage(0, 0)
        stop_reason = StopReason.NATURAL

        async for event in self.run(
            task, mode="normal", plan_content="", _inject=not already_injected
        ):
            if observer is not None:
                observer(event)
            if event.type == EventType.TEXT:
                final_text += event.payload
            elif event.type == EventType.TOOL_CALL:
                tool_count += 1
            elif event.type == EventType.TOKEN_USAGE:
                tu = event.payload
                usage = TokenUsage(
                    usage.input_tokens + tu.input_tokens,
                    usage.output_tokens + tu.output_tokens,
                    usage.cache_creation_input_tokens + tu.cache_creation_input_tokens,
                    usage.cache_read_input_tokens + tu.cache_read_input_tokens,
                )
            elif event.type == EventType.DONE:
                stop_reason = event.payload

        if stop_reason == StopReason.MAX_TURNS:
            # 局部 import 避循环（subagent.__init__ 可能 import manager → agent，T12）
            from ..subagent.errors import MaxTurnsReached

            raise MaxTurnsReached(final_text, usage, tool_count)
        if stop_reason == StopReason.CANCELLED:
            raise asyncio.CancelledError("subagent cancelled")
        if stop_reason == StopReason.STREAM_ERROR:
            raise RuntimeError("subagent stream error")
        if stop_reason == StopReason.CONSECUTIVE_UNKNOWN_TOOLS:
            raise RuntimeError("subagent consecutive unknown tools")
        return final_text

    def _schedule_memory_update(self, user_input: str) -> None:
        manager = self._memory_manager
        if manager is None:
            return
        explicit = any(
            word in user_input.lower()
            for word in ("记住", "记忆", "别忘", "remember", "memo")
        )
        if not explicit and self._natural_rounds % 5:
            return
        if self._memory_task is not None and not self._memory_task.done():
            return
        try:
            messages = self.conv.get_context()
            session_id = getattr(manager, "session_id", "")
            self._memory_task = asyncio.create_task(
                self._run_memory_update(manager, messages, session_id)
            )
        except (RuntimeError, TypeError, AttributeError):
            self._memory_task = None

    async def _run_memory_update(self, manager, messages, session_id: str) -> None:
        try:
            try:
                await manager.update_async(messages, session_id=session_id)
            except TypeError:
                await manager.update_async(messages)
        except Exception:  # noqa: BLE001 - memory is best effort
            return

    async def run_force_compact(
        self, tool_defs: list[ToolDefinition]
    ) -> object:  # CompactOutcome（延迟导入避循环）
        """手动 /compact 入口：持 _run_lock 等主循环释放，调 compact_now（F34）。"""
        async with self._run_lock:
            if self._context_mgr is None:
                from ..context.summarize import CompactOutcome

                return CompactOutcome(True, 0, 0, 0, False, "no context", None)
            # ch12 ③ 手动压缩路径 pre/post_compact（trigger=manual）
            await self._dispatch_hook(HookEvent.PRE_COMPACT, {"trigger": "manual"})
            try:
                return await self._context_mgr.compact_now(tool_defs)
            finally:
                await self._dispatch_hook(HookEvent.POST_COMPACT, {"trigger": "manual"})

    def _drain_context_events(self):
        """Translate ContextManager callbacks into public Agent events."""
        events = self._context_events
        self._context_events = []
        mapping = {
            "context_compacting": EventType.CONTEXT_COMPACTING,
            "compact_failed": EventType.COMPACT_FAILED,
            "context_offloaded": EventType.CONTEXT_OFFLOADED,
            "context_compacted": EventType.CONTEXT_COMPACTED,
        }
        return (
            Event(mapping[kind], payload) for kind, payload in events if kind in mapping
        )

    def _attach_request_trace(
        self, payload, user_input: str, turn: int, run_id: str
    ) -> None:
        if self._context_mgr is None:
            return
        prepare = getattr(self._context_mgr, "prepare_request_trace", None)
        if prepare is None:
            return
        try:
            try:
                payload.trace_context = prepare(user_input, turn, run_id)
            except TypeError:
                payload.trace_context = prepare(user_input, turn)
        except (AttributeError, OSError, RuntimeError, TypeError):
            # Request tracing is observability only and must never break a turn.
            payload.trace_context = None


def _strip_truncation(output: str) -> str:
    """剥离 read_file 截断提示行，返回纯净内容。"""
    truncation_marker = "…（已截断"
    if truncation_marker in output:
        return output[: output.rfind(truncation_marker)].rstrip("\n")
    return output
