"""Agent 单轮闭环编排"""

from typing import AsyncIterator

from ..provider.base import Provider, ToolCall
from ..conversation.manager import ConversationManager
from ..tools.registry import Registry
from .events import Event, EventType


class Agent:
    """承载单轮闭环编排：请求#1（带工具）→ 执行 → 回灌 → 请求#2（续答）→ 停"""

    def __init__(
        self,
        provider: Provider,
        conversation: ConversationManager,
        registry: Registry,
    ) -> None:
        self.provider = provider
        self.conv = conversation
        self.registry = registry

    async def run(self, user_input: str) -> AsyncIterator[Event]:
        """对外吐出统一 Event 流"""
        self.conv.add_user(user_input)
        tool_defs = self.registry.to_definitions()

        # ── 请求 #1：带工具定义 ──
        stream1 = self.provider.stream(self.conv.get_context(), tools=tool_defs)
        buffer = ""
        tool_call_emitted = False

        async for event in stream1:
            if event.text:
                buffer += event.text
                yield Event(EventType.TEXT, event.text)
            elif event.tool_call:
                tool_call_emitted = True
                yield Event(EventType.TOOL_CALL, event.tool_call)
                # 执行工具
                result = await self.registry.execute(
                    event.tool_call.tool_name,
                    event.tool_call.arguments,
                )
                yield Event(EventType.TOOL_RESULT, result)
                # 回灌对话历史
                self.conv.add_tool_call(event.tool_call)
                self.conv.add_tool_result(result)
                break
            elif event.done:
                if not tool_call_emitted:
                    self.conv.add_assistant(buffer)
                yield Event(EventType.DONE, "")
                return
            elif event.err:
                yield Event(EventType.ERROR, event.err)
                return

        if not tool_call_emitted:
            # 请求#1 正常结束且未调用工具
            yield Event(EventType.DONE, "")
            return

        # ── 请求 #2：续答（不带工具）──
        stream2 = self.provider.stream(self.conv.get_context(), tools=None)
        buffer = ""

        async for event in stream2:
            if event.text:
                buffer += event.text
                yield Event(EventType.TEXT, event.text)
            elif event.tool_call:
                # 防御：请求#2 不应出现 tool_call（未传 tools）
                # 忽略并继续消费文本
                continue
            elif event.done:
                self.conv.add_assistant(buffer)
                yield Event(EventType.DONE, "")
                return
            elif event.err:
                yield Event(EventType.ERROR, event.err)
                return
