"""Agent ReAct 循环测试（ch04）"""

import pytest
from mewcode.agent import Agent, EventType, StopReason
from mewcode.agent.events import Event
from mewcode.provider.base import Message, StreamEvent, ToolCall, ToolResult, ToolDefinition, TokenUsage
from mewcode.conversation.manager import ConversationManager
from mewcode.tools.registry import Registry


class MockProvider:
    """模拟 Provider，支持工具调用和纯文本两种模式，兼容 ch04 system_suffix 签名"""

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

    async def stream(self, msgs, tools=None, system_suffix=""):
        if self._mode == "text":
            yield StreamEvent(text="hello")
            yield StreamEvent(done=True, usage=TokenUsage(10, 5))
        elif self._mode == "tool":
            yield StreamEvent(
                tool_call=ToolCall(tool_name="mock_tool", arguments={"x": 1})
            )
            yield StreamEvent(done=True, usage=TokenUsage(10, 5))


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

    @property
    def read_only(self):
        return True

    async def execute(self, arguments):
        return ToolResult(status="ok", output="mock_result")


@pytest.mark.anyio
async def test_agent_text_only():
    """纯文本回复：不调用工具，自然终止"""
    provider = MockProvider(mode="text")
    conv = ConversationManager("", 20)
    registry = Registry()
    agent = Agent(provider, conv, registry)

    events = []
    async for e in agent.run("hi"):
        events.append(e)

    types = [e.type for e in events]
    assert EventType.TURN_START in types
    assert EventType.TEXT in types
    assert EventType.TOKEN_USAGE in types
    assert EventType.DONE in types
    # DONE payload 是 StopReason
    done_event = events[-1]
    assert done_event.payload == StopReason.NATURAL


@pytest.mark.anyio
async def test_agent_tool_call():
    """工具调用闭环：多轮 ReAct 循环"""
    provider = MockProvider(mode="tool")
    conv = ConversationManager("", 20)
    registry = Registry()
    registry.register(MockTool())

    # 第 1 次请求返回工具调用，第 2 次返回纯文本
    call_count = [0]

    async def patched_stream(msgs, tools=None, system_suffix=""):
        call_count[0] += 1
        if call_count[0] == 1:
            yield StreamEvent(
                tool_call=ToolCall(tool_name="mock_tool", arguments={"x": 1})
            )
            yield StreamEvent(done=True, usage=TokenUsage(20, 10))
        else:
            yield StreamEvent(text="result processed")
            yield StreamEvent(done=True, usage=TokenUsage(30, 15))

    provider.stream = patched_stream

    agent = Agent(provider, conv, registry)
    events = []
    async for e in agent.run("use tool"):
        events.append(e)

    types = [e.type for e in events]
    assert EventType.TURN_START in types
    assert EventType.TOOL_CALL in types
    assert EventType.TOOL_RESULT in types
    assert EventType.TOKEN_USAGE in types
    assert EventType.TURN_END in types
    assert EventType.TEXT in types
    assert EventType.DONE in types

    # 验证对话历史
    msgs = conv.get_context()
    roles = [m.role for m in msgs]
    assert "user" in roles
    assert "assistant" in roles
    assert "tool" in roles


@pytest.mark.anyio
async def test_agent_max_turns():
    """达到迭代上限时强制终止"""
    provider = MockProvider(mode="tool")
    conv = ConversationManager("", 20)
    registry = Registry()
    registry.register(MockTool())

    # 始终返回工具调用，永不自然终止
    async def infinite_tools(msgs, tools=None, system_suffix=""):
        yield StreamEvent(
            tool_call=ToolCall(tool_name="mock_tool", arguments={"x": 1})
        )
        yield StreamEvent(done=True, usage=TokenUsage(10, 5))

    provider.stream = infinite_tools

    agent = Agent(provider, conv, registry)
    events = []
    async for e in agent.run("loop forever"):
        events.append(e)

    done_event = events[-1]
    assert done_event.type == EventType.DONE
    assert done_event.payload == StopReason.MAX_TURNS


@pytest.mark.anyio
async def test_agent_cancel():
    """取消信号应终止循环"""
    provider = MockProvider(mode="tool")
    conv = ConversationManager("", 20)
    registry = Registry()
    registry.register(MockTool())

    # 模拟：工具执行期间取消
    async def slow_tool(msgs, tools=None, system_suffix=""):
        yield StreamEvent(
            tool_call=ToolCall(tool_name="mock_tool", arguments={"x": 1})
        )
        yield StreamEvent(done=True, usage=TokenUsage(10, 5))

    provider.stream = slow_tool

    agent = Agent(provider, conv, registry)

    events = []
    async for e in agent.run("use tool"):
        events.append(e)
        if e.type == EventType.TOOL_RESULT:
            agent.cancel()

    done_event = events[-1]
    assert done_event.type == EventType.DONE
    assert done_event.payload == StopReason.CANCELLED


@pytest.mark.anyio
async def test_agent_unknown_tool_stop():
    """连续未知工具应终止"""
    provider = MockProvider(mode="tool")
    conv = ConversationManager("", 20)
    registry = Registry()  # 空注册表，所有工具都是未知的

    # 始终返回未知工具
    async def unknown_tool(msgs, tools=None, system_suffix=""):
        yield StreamEvent(
            tool_call=ToolCall(tool_name="unknown_tool", arguments={})
        )
        yield StreamEvent(done=True, usage=TokenUsage(10, 5))

    provider.stream = unknown_tool

    agent = Agent(provider, conv, registry)
    events = []
    async for e in agent.run("use unknown"):
        events.append(e)

    done_event = events[-1]
    assert done_event.type == EventType.DONE
    assert done_event.payload == StopReason.CONSECUTIVE_UNKNOWN_TOOLS


@pytest.mark.anyio
async def test_agent_stream_error():
    """流式错误应终止"""
    provider = MockProvider(mode="tool")
    conv = ConversationManager("", 20)
    registry = Registry()

    async def error_stream(msgs, tools=None, system_suffix=""):
        yield StreamEvent(err=RuntimeError("network error"))

    provider.stream = error_stream

    agent = Agent(provider, conv, registry)
    events = []
    async for e in agent.run("test"):
        events.append(e)

    types = [e.type for e in events]
    assert EventType.ERROR in types
    assert EventType.DONE in types
    done_event = events[-1]
    assert done_event.payload == StopReason.STREAM_ERROR


@pytest.mark.anyio
async def test_agent_plan_mode():
    """Plan Mode 应只使用只读工具"""
    provider = MockProvider(mode="text")
    conv = ConversationManager("", 20)
    registry = Registry()
    registry.register(MockTool())  # mock_tool is read_only=True

    agent = Agent(provider, conv, registry)
    events = []
    async for e in agent.run("analyze", mode="plan"):
        events.append(e)

    done_event = events[-1]
    assert done_event.payload == StopReason.NATURAL