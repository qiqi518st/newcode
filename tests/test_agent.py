"""Agent 闭环测试"""

import pytest
from mewcode.agent import Agent, EventType
from mewcode.agent.events import Event
from mewcode.provider.base import Message, StreamEvent, ToolCall, ToolResult, ToolDefinition
from mewcode.conversation.manager import ConversationManager
from mewcode.tools.registry import Registry


class MockProvider:
    """模拟 Provider，支持工具调用和纯文本两种模式"""

    def __init__(self, mode="text"):
        self._mode = mode
        self._name = "mock"
        self._model = "mock-model"

    @property
    def name(self):
        return self._name

    @property
    def model(self):
        return self._model

    async def stream(self, msgs, tools=None):
        if self._mode == "text":
            yield StreamEvent(text="hello")
            yield StreamEvent(done=True)
        elif self._mode == "tool":
            yield StreamEvent(
                tool_call=ToolCall(tool_name="mock_tool", arguments={"x": 1})
            )
            yield StreamEvent(done=True)


class MockTool:
    @property
    def name(self):
        return "mock_tool"

    @property
    def description(self):
        return "a mock tool"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}}

    async def execute(self, arguments):
        return ToolResult(status="ok", output="mock_result")


@pytest.mark.anyio
async def test_agent_text_only():
    """纯文本回复：不调用工具"""
    provider = MockProvider(mode="text")
    conv = ConversationManager("", 20)
    registry = Registry()
    agent = Agent(provider, conv, registry)

    events = []
    async for e in agent.run("hi"):
        events.append(e)

    assert events[0].type == EventType.TEXT
    assert events[0].payload == "hello"
    assert events[1].type == EventType.DONE


@pytest.mark.anyio
async def test_agent_tool_call():
    """工具调用闭环"""
    provider = MockProvider(mode="tool")
    conv = ConversationManager("", 20)
    registry = Registry()
    registry.register(MockTool())

    # 第 2 次请求返回纯文本
    call_count = [0]
    original_stream = provider.stream

    async def patched_stream(msgs, tools=None):
        call_count[0] += 1
        if call_count[0] == 1:
            yield StreamEvent(tool_call=ToolCall(tool_name="mock_tool", arguments={"x": 1}))
            yield StreamEvent(done=True)
        else:
            yield StreamEvent(text="result processed")
            yield StreamEvent(done=True)

    provider.stream = patched_stream

    agent = Agent(provider, conv, registry)
    events = []
    async for e in agent.run("use tool"):
        events.append(e)

    types = [e.type for e in events]
    assert EventType.TOOL_CALL in types
    assert EventType.TOOL_RESULT in types
    assert EventType.TEXT in types
    assert EventType.DONE in types

    # 验证对话历史
    msgs = conv.get_context()
    roles = [m.role for m in msgs]
    assert "user" in roles
    assert "assistant" in roles
    assert "tool" in roles
