"""Agent ReAct 循环引擎：ch06 五层权限系统集成"""

import asyncio
from collections.abc import AsyncIterator

from ..conversation.manager import ConversationManager
from ..permission.checker import PermissionChecker, extract_target, friendly_name
from ..permission.hitl import HITLRequest, HITLResponse
from ..permission.types import Decision
from ..prompt.assembler import PayloadAssembler
from ..prompt.reminders import plan_mode_reminder
from ..prompt.resources import EXECUTE_DIRECTIVE
from ..provider.base import Provider, ToolCall, ToolResult
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
    ) -> None:
        self.provider = provider
        self.conv = conversation
        self.registry = registry
        self._stable_prompt = stable_prompt  # 段1 稳定系统提示（会话内不变，可缓存）
        self._env_segment = env_segment  # 段2 环境信息（会话内不变，不缓存）
        self._assembler = PayloadAssembler()
        self._scheduler = ToolScheduler(registry)
        self._cancelled = asyncio.Event()

        # 权限系统
        self._permission = permission
        self._interactive = is_interactive
        self._hitl_event = asyncio.Event()
        self._hitl_response: HITLResponse | None = None

    @property
    def permission(self) -> PermissionChecker | None:
        return self._permission

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
    ) -> AsyncIterator[Event]:
        """ReAct 循环入口，对外吐出统一 Event 流

        mode: "normal" | "plan" | "execute"
        plan_content: /do 时注入的计划文件内容
        """
        # 重置取消信号
        self._cancelled.clear()

        # 注入用户消息
        if mode == "execute" and plan_content:
            directive = EXECUTE_DIRECTIVE.format(plan=plan_content)
            self.conv.add_user(directive)
        else:
            self.conv.add_user(user_input)

        # 选择工具集（plan 模式暴露全部工具，由 SystemPrompt 引导自觉只读）
        tool_defs = self.registry.to_definitions()

        # 未知工具连续计数
        _unknown_streak: int = 0

        # ── ReAct 循环 ──
        for turn in range(_MAX_AGENT_TURNS):
            # 每轮开始前检查取消
            if self._cancelled.is_set():
                yield Event(EventType.DONE, StopReason.CANCELLED)
                return

            yield Event(EventType.TURN_START, turn)

            # 轮次级补充消息：plan 模式按轮注入（第 0/5 轮完整，其余精简，瞬时不持久）
            reminders = [plan_mode_reminder(turn)] if mode == "plan" else []

            # 组装管线：稳定提示(段1) + 环境(段2) + 历史 + reminders + tools → PromptPayload
            payload = self._assembler.assemble(
                self._stable_prompt,
                self._env_segment,
                self.conv.get_context(),
                reminders,
                tool_defs if tool_defs else None,
            )

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
                yield Event(EventType.ERROR, _stream_error)
                yield Event(EventType.DONE, StopReason.STREAM_ERROR)
                return

            # Token 用量
            if _token_usage:
                yield Event(EventType.TOKEN_USAGE, _token_usage)

            # 自然终止：无工具调用
            if not _tool_calls:
                if _buffer:
                    self.conv.add_assistant(_buffer)
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

            # ── 权限检查（ch06）──
            # 对每个 known_call 做权限检查，分出 allowed_calls
            allowed_calls: list[ToolCall] = []
            permission_results: list[tuple[ToolCall, Decision, str]] = []

            for tc in known_calls:
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
                    permission_results.append((tc, Decision.DENY, result.reason))
                elif result.decision == Decision.ASK:
                    if not self._interactive:
                        # 非交互：直接转为 DENY
                        tr = ToolResult(status="error", error=result.reason)
                        yield Event(EventType.TOOL_CALL, tc)
                        yield Event(EventType.TOOL_RESULT, tr)
                        self.conv.add_tool_result(tc, tr)
                        permission_results.append((tc, Decision.DENY, result.reason))
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
                            permission_results.append((tc, Decision.DENY, "用户拒绝"))
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
                            # 写入历史
                            self.conv.add_tool_result(tc, sr.result)
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
        yield Event(EventType.DONE, StopReason.MAX_TURNS)
