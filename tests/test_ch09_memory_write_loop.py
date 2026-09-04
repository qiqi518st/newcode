"""ch09 记忆写入闭环接线测试（spec F13 显式「记住」侧端到端）

背景：Agent 收到「记住 X」时曾退化用 Bash 手动写记忆文件——用户级记忆在工作区外
被 L2 沙箱拦截（write_file DENY），Bash 又弹权限确认且中文乱码。现在 Agent 应
调用 write_memory 工具（MEMORY 类别四档免确认）完成写入。
防 bug：Agent 仍用文件/Bash 工具写记忆；write_memory 结果未进入对话历史。
"""

import pytest

from newcode.agent import Agent, EventType
from newcode.conversation.manager import ConversationManager
from newcode.memory.manager import MemoryManager
from newcode.provider.base import StreamEvent, TokenUsage, ToolCall
from newcode.tools.memory_write import WriteMemoryTool
from newcode.tools.registry import Registry


class _WriteProvider:
    """第 1 次请求返回 write_memory 调用，第 2 次返回文本回复"""

    def __init__(self):
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
                tool_call=ToolCall(
                    tool_name="write_memory",
                    arguments={
                        "type": "user_preference",
                        "title": "Python 初学者",
                        "content": "用户是 Python 新手，正在学习 Python。讲解时用简单语言。",
                    },
                )
            )
            yield StreamEvent(done=True, usage=TokenUsage(20, 10))
        else:
            yield StreamEvent(text="好的，已记住。")
            yield StreamEvent(done=True, usage=TokenUsage(10, 5))


@pytest.mark.anyio
async def test_agent_writes_memory_via_tool(tmp_path):
    """端到端：显式记住请求 → Agent 调 write_memory → 记忆落盘并进入对话历史。"""
    manager = MemoryManager(tmp_path / "proj", tmp_path / "user")
    registry = Registry()
    registry.register(WriteMemoryTool(manager))

    provider = _WriteProvider()
    conv = ConversationManager(20)
    agent = Agent(
        provider,
        conv,
        registry,
        "你是 NewCode。长期记忆索引：无。当用户要求记住信息时用 write_memory。",
        "mock-env",
    )

    events = []
    async for e in agent.run("帮我记住：我是 Python 新手"):
        events.append(e)

    # 工具被识别为已知工具并成功执行
    results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert results and all(e.payload.status == "ok" for e in results)

    # 记忆真实落盘（用户级），UTF-8 内容正确
    notes = manager.user_store.list_notes()
    assert len(notes) == 1
    assert notes[0].title == "Python 初学者"
    assert "正在学习" in (notes[0].content or "")
    assert "Python 初学者" in manager.user_store.load_index()
    assert not manager.project_store.list_notes()

    # 写入结果进入下一轮上下文
    assert "记忆已创建" in str(provider.payloads[-1].messages)

    # 自然终止
    assert events[-1].type == EventType.DONE
