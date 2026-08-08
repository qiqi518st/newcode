"""Agent ReAct 循环引擎：替代 ch03 的单轮闭环"""

import asyncio
from typing import AsyncIterator

from ..provider.base import Provider, ToolCall, ToolResult
from ..conversation.manager import ConversationManager
from ..tools.registry import Registry
from ..prompt.resources import PLAN_MODE_REMINDER, EXECUTE_DIRECTIVE
from .events import Event, EventType, StopReason, TokenUsage, TurnEnd
from .scheduler import ToolScheduler, ScheduledResult

# 内置常量：最大迭代轮数
_MAX_AGENT_TURNS: int = 10


class Agent:
    """ReAct 循环编排：while 迭代直到自然完成 / 上限 / 取消 / 连续未知工具 / 出错"""

    def __init__(
        self,
        provider: Provider,
        conversation: ConversationManager,
        registry: Registry,
    ) -> None:
        self.provider = provider
        self.conv = conversation
        self.registry = registry
        self._scheduler = ToolScheduler(registry)
        self._cancelled = asyncio.Event()

    def cancel(self) -> None:
        """设置取消信号，TUI 在 ESC/Ctrl+C 时调用"""
        self._cancelled.set()

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

        # 选择工具集
        if mode == "plan":
            tool_defs = self.registry.read_only_definitions()
            system_suffix = PLAN_MODE_REMINDER
        else:
            tool_defs = self.registry.to_definitions()
            system_suffix = ""

        # 未知工具连续计数
        _unknown_streak: int = 0

        # ── ReAct 循环 ──
        for turn in range(_MAX_AGENT_TURNS):
            # 每轮开始前检查取消
            if self._cancelled.is_set():
                yield Event(EventType.DONE, StopReason.CANCELLED)
                return

            yield Event(EventType.TURN_START, turn)

            # ── 发起 LLM 请求 ──
            stream = self.provider.stream(
                self.conv.get_context(),
                tools=tool_defs if tool_defs else None,
                system_suffix=system_suffix,
            )

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

            # 分类已知/未知工具（plan mode 额外过滤非只读工具）
            known_calls: list[ToolCall] = []
            unknown_calls: list[ToolCall] = []

            for tc in _tool_calls:
                tool = self.registry.get(tc.tool_name)
                if tool is None:
                    unknown_calls.append(tc)
                elif mode == "plan" and not tool.read_only:
                    # plan mode 安全网：模型可能幻觉调用非只读工具
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
                        ToolResult(
                            status="error",
                            error=f"未知工具: {tc.tool_name}",
                        )
                    )
                yield Event(EventType.DONE, StopReason.CONSECUTIVE_UNKNOWN_TOOLS)
                return

            # 已知工具：写入 assistant 消息
            self.conv.add_assistant_with_tool_calls(_buffer, _tool_calls)

            # 先写入未知工具的结果到历史
            for tc in unknown_calls:
                self.conv.add_tool_result(
                    ToolResult(
                        status="error",
                        error=f"未知工具: {tc.tool_name}",
                    )
                )

            # 执行已知工具
            if known_calls:
                scheduled = await self._scheduler.schedule(known_calls)

                # 按原始顺序产出 TOOL_CALL + TOOL_RESULT 事件
                # scheduled 保持 known_calls 的顺序，按原始位置与 unknown 交叉
                known_idx = 0
                known_results: dict[int, ScheduledResult] = {
                    idx: sr for idx, sr in enumerate(scheduled)
                }

                for tc in _tool_calls:
                    if self.registry.get(tc.tool_name) is not None:
                        sr = known_results[known_idx]
                        yield Event(EventType.TOOL_CALL, tc)
                        yield Event(EventType.TOOL_RESULT, sr.result)
                        # 写入历史
                        self.conv.add_tool_result(sr.result)
                        known_idx += 1
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
                # 补「已取消」结果给未完成的工具（本轮已全部执行，无未完成）
                yield Event(EventType.DONE, StopReason.CANCELLED)
                return

        # 达到迭代上限
        if _buffer:
            self.conv.add_assistant(_buffer)
        yield Event(EventType.DONE, StopReason.MAX_TURNS)