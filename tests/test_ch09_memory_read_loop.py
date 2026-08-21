"""ch09 记忆读取闭环接线测试（spec F13 加载闭环的端到端）

背景：read_file 被沙箱锁在工作区内，用户级记忆（~/.mewcode/memory/）读不到。
新增 read_memory 工具后，Agent 在对话中看到索引行、判定相关、调用 read_memory
拉取全文这条真实代码路径必须工作——工具结果要进入对话历史，供后续轮次使用。
防 bug：工具未注册导致 Agent 收到「未知工具」；工具结果未回填对话导致下一轮
payload 里没有记忆内容。
"""

import pytest

from mewcode.agent import Agent, EventType
from mewcode.conversation.manager import ConversationManager
from mewcode.memory.manager import MemoryManager
from mewcode.memory.models import MemoryOperation
from mewcode.provider.base import StreamEvent, TokenUsage, ToolCall
from mewcode.tools.memory_read import ReadMemoryTool
from mewcode.tools.registry import Registry


class _MemProvider:
    """模拟 Provider：第 1 次请求返回 read_memory 调用，第 2 次返回文本回复"""

    def __init__(self, tool_name, arguments):
        self._tool_name = tool_name
        self._arguments = arguments
        self._name = "mock"
        self._model = "mock-model"
        self.payloads = []

    @property
    def name(self):
        return self._name

    @property
    def model(self):
        return self._model

    async def stream(self, payload):
        self.payloads.append(payload)
        if len(self.payloads) == 1:
            yield StreamEvent(
                tool_call=ToolCall(tool_name=self._tool_name, arguments=self._arguments)
            )
            yield StreamEvent(done=True, usage=TokenUsage(20, 10))
        else:
            yield StreamEvent(
                text="根据记忆，你的 python 水平是中级，熟悉 pandas/numpy。"
            )
            yield StreamEvent(done=True, usage=TokenUsage(10, 5))


@pytest.mark.anyio
async def test_agent_reads_user_memory_via_tool(tmp_path):
    """端到端：用户级 python 记忆 → Agent 调 read_memory 读全文 → 全文进入对话历史。"""
    # 准备：用户级记忆文件 + MemoryManager + 注册 read_memory 工具
    user_dir = tmp_path / "user_memory"
    manager = MemoryManager(tmp_path / "proj", user_dir)
    manager.user_store.apply(
        MemoryOperation(
            action="create",
            level="user",
            type="user_preference",
            title="python 水平",
            slug="python-level",
            content="用户 python 水平：中级，熟悉 pandas/numpy，不熟悉 asyncio。",
        )
    )
    registry = Registry()
    registry.register(ReadMemoryTool(manager))

    provider = _MemProvider(
        "read_memory", {"filename": "user_preference_python-level.md"}
    )
    conv = ConversationManager(20)
    agent = Agent(
        provider,
        conv,
        registry,
        "你是 MewCode。长期记忆索引：\n"
        "- [user_preference] python 水平 (user_preference_python-level.md) - 用户 python 水平：中级\n",
        "mock-env",
    )

    events = []
    async for e in agent.run("我的 python 水平怎么样"):
        events.append(e)

    # 工具调用被识别为已知工具，未报「未知工具」
    tool_result_events = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert tool_result_events, "Agent 应执行 read_memory 工具"
    assert all(e.payload.status == "ok" for e in tool_result_events)

    # 记忆全文进入了对话历史：下一次请求的上下文里应包含记忆正文
    text = str(provider.payloads[-1].messages)
    assert "熟悉 pandas/numpy" in text
    assert "中级" in text

    # 自然终止
    assert events[-1].type == EventType.DONE
