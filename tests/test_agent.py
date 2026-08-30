"""Agent ReAct 循环测试（ch04 用例保留 + ch05 组装管线适配）"""

import pytest

from mewcode.agent import Agent, EventType, StopReason
from mewcode.conversation.manager import ConversationManager
from mewcode.provider.base import (
    StreamEvent,
    TokenUsage,
    ToolCall,
    ToolResult,
)
from mewcode.tools.registry import Registry


class MockProvider:
    """模拟 Provider：接收 PromptPayload，支持工具调用和纯文本两种模式"""

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

    async def stream(self, payload):
        self.last_payload = payload
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


def _agent(provider, registry=None):
    """构造 Agent，传入 ch05 的 stable_prompt / env_segment"""
    conv = ConversationManager(20)
    return Agent(provider, conv, registry or Registry(), "mock-stable", "mock-env")


@pytest.mark.anyio
async def test_agent_text_only():
    """纯文本回复：不调用工具，自然终止"""
    provider = MockProvider(mode="text")
    registry = Registry()
    agent = _agent(provider, registry)

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
    registry = Registry()
    registry.register(MockTool())
    agent = _agent(provider, registry)

    # 第 1 次请求返回工具调用，第 2 次返回纯文本
    call_count = [0]

    async def patched_stream(payload):
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
    msgs = agent.conv.get_context()
    roles = [m.role for m in msgs]
    assert "user" in roles
    assert "assistant" in roles
    assert "tool" in roles


@pytest.mark.anyio
async def test_agent_max_turns():
    """达到迭代上限时强制终止"""
    provider = MockProvider(mode="tool")
    registry = Registry()
    registry.register(MockTool())
    agent = _agent(provider, registry)

    # 始终返回工具调用，永不自然终止
    async def infinite_tools(payload):
        yield StreamEvent(tool_call=ToolCall(tool_name="mock_tool", arguments={"x": 1}))
        yield StreamEvent(done=True, usage=TokenUsage(10, 5))

    provider.stream = infinite_tools

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
    registry = Registry()
    registry.register(MockTool())
    agent = _agent(provider, registry)

    # 模拟：工具执行期间取消
    async def slow_tool(payload):
        yield StreamEvent(tool_call=ToolCall(tool_name="mock_tool", arguments={"x": 1}))
        yield StreamEvent(done=True, usage=TokenUsage(10, 5))

    provider.stream = slow_tool

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
    registry = Registry()  # 空注册表，所有工具都是未知的
    agent = _agent(provider, registry)

    # 始终返回未知工具
    async def unknown_tool(payload):
        yield StreamEvent(tool_call=ToolCall(tool_name="unknown_tool", arguments={}))
        yield StreamEvent(done=True, usage=TokenUsage(10, 5))

    provider.stream = unknown_tool

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
    registry = Registry()
    agent = _agent(provider, registry)

    async def error_stream(payload):
        yield StreamEvent(err=RuntimeError("network error"))

    provider.stream = error_stream

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
    """Plan Mode 应能正常运行（ch06: 暴露全部工具，由 SystemPrompt 引导自觉只读）"""
    provider = MockProvider(mode="text")
    registry = Registry()
    registry.register(MockTool())  # mock_tool is read_only=True

    agent = _agent(provider, registry)
    events = []
    async for e in agent.run("analyze", mode="plan"):
        events.append(e)

    done_event = events[-1]
    assert done_event.payload == StopReason.NATURAL


# ── ch05 新增：组装管线与按轮注入 ──


@pytest.mark.anyio
async def test_agent_payload_routes_stable_and_env():
    """stable_prompt 与 env_segment 路由进 payload"""
    provider = MockProvider(mode="text")
    agent = _agent(provider, Registry())
    async for _ in agent.run("hi"):
        pass
    assert provider.last_payload.stable_prompt == "mock-stable"
    assert provider.last_payload.env_segment == "mock-env"


@pytest.mark.anyio
async def test_agent_plan_mode_injects_reminder():
    """plan 模式下每轮注入 system-reminder（瞬时不持久）"""
    provider = MockProvider(mode="text")
    agent = _agent(provider, Registry())
    payloads = []

    async def capturing_stream(payload):
        payloads.append(payload)
        yield StreamEvent(text="plan...")
        yield StreamEvent(done=True, usage=TokenUsage(10, 5))

    provider.stream = capturing_stream
    async for _ in agent.run("analyze", mode="plan"):
        pass

    # 第 0 轮应注入完整版提醒（带 <system-reminder> 标签）
    assert payloads, "应至少捕获一次请求"
    assert payloads[0].reminders
    assert "<system-reminder>" in payloads[0].reminders[0].content

    # 提醒不写入持久历史：历史里不含 <system-reminder> 内容
    # （user 输入 + assistant 回复是正常持久历史）
    history = agent.conv.get_context()
    assert all("<system-reminder>" not in (m.content or "") for m in history)
    assert history[0].role == "user" and history[-1].role == "assistant"


@pytest.mark.anyio
async def test_agent_normal_mode_no_reminders():
    """普通模式不注入 plan-mode 提醒"""
    provider = MockProvider(mode="text")
    agent = _agent(provider, Registry())
    payloads = []

    async def capturing_stream(payload):
        payloads.append(payload)
        yield StreamEvent(text="ok")
        yield StreamEvent(done=True, usage=TokenUsage(10, 5))

    provider.stream = capturing_stream
    async for _ in agent.run("hi"):
        pass
    assert payloads
    assert payloads[0].reminders == []
