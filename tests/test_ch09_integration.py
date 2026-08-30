"""ch09 集成测试（spec F5/F7/F9/F12/F13/F24-F27 / AC9/AC16/AC24/AC26-AC27）。

防 bug：恢复后新消息追加到原 JSONL、compact 回调顺序错误、
PromptBuilder 两个新 section 优先级/空内容处理错误、
Agent 自然完成不触发记忆或关键词不触发、记忆更新阻塞下一条输入。
"""

import asyncio
import json
from pathlib import Path

import pytest

from mewcode.agent import Agent, EventType, StopReason
from mewcode.conversation.manager import ConversationManager
from mewcode.prompt.builder import PromptBuilder, Section
from mewcode.provider.base import Message, StreamEvent, TokenUsage
from mewcode.session.runtime import SessionRuntime
from mewcode.tools.registry import Registry

# ---------- ConversationManager 回调 ----------


def test_conversation_callbacks_append(tmp_path):
    """防 bug：追加回调次数与参数正确，未设置回调时行为不变（AC9）。"""
    appended = []
    replaced = []
    cm = ConversationManager(20, on_append=appended.append, on_replace=replaced.append)
    cm.add_user("hi")
    cm.add_assistant("hello")
    assert len(appended) == 2
    assert appended[0].role == "user" and appended[0].content == "hi"
    assert appended[1].role == "assistant"

    # 未设置回调时兼容
    plain = ConversationManager(20)
    plain.add_user("x")
    assert plain.get_context() == [Message(role="user", content="x")]


def test_conversation_replace_callback_order(tmp_path):
    """防 bug：replace_history 先回调且传入新消息列表副本。"""
    replaced = []
    cm = ConversationManager(20, on_replace=replaced.append)
    new = [Message(role="user", content="s1"), Message(role="assistant", content="a1")]
    cm.replace_history(new)
    assert len(replaced) == 1
    assert replaced[0] == new
    assert replaced[0] is not new  # 副本


# ---------- SessionRuntime 新建/恢复/追加 ----------


def test_runtime_create_new_writes_jsonl(tmp_path):
    """防 bug：新建会话的追加消息落盘到 conversation.jsonl。"""
    rt = SessionRuntime(tmp_path, model="mock-model")
    cm = rt.create_new()
    cm.add_user("u1")
    cm.add_assistant("a1")
    rt.close()
    lines = Path(rt.context.conversation_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["role"] == "user"
    assert json.loads(lines[1])["role"] == "assistant"


@pytest.mark.anyio
async def test_runtime_resume_then_append_same_file(tmp_path):
    """防 bug：恢复会话后新消息追加到原 JSONL，原新 session 文件保留（AC16）。"""
    rt = SessionRuntime(tmp_path, model="mock-model")
    cm = rt.create_new()
    cm.add_user("before")
    cm.add_assistant("reply")
    session_id = rt.session_id
    rt.close()

    # 模拟「重启」：新 runtime 恢复同一会话
    rt2 = SessionRuntime(tmp_path, model="mock-model")
    await rt2.resume(session_id)
    rt2.conversation.add_user("after")
    rt2.close()

    convo_path = Path(rt2.context.conversation_path)
    lines = convo_path.read_text(encoding="utf-8").splitlines()
    contents = [json.loads(l)["content"] for l in lines]
    assert contents == ["before", "reply", "after"]
    # 恢复时初始消息不重复写入
    assert len(lines) == 3


@pytest.mark.anyio
async def test_runtime_resume_does_not_rewrite_initial(tmp_path):
    """防 bug：恢复的初始消息不得重新追加到 JSONL（T9 要求）。"""
    rt = SessionRuntime(tmp_path, model="m")
    cm = rt.create_new()
    cm.add_user("only-one")
    sid = rt.session_id
    rt.close()

    rt2 = SessionRuntime(tmp_path, model="m")
    cm2 = await rt2.resume(sid)
    assert len(cm2.get_context()) == 1
    lines = Path(rt2.context.conversation_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # 恢复没重复写


def test_runtime_compact_marker_before_messages(tmp_path):
    """防 bug：replace_history 触发 compact 标记先写、压缩消息后写（AC8）。"""
    rt = SessionRuntime(tmp_path)
    cm = rt.create_new()
    cm.add_user("u1")
    cm.replace_history([Message(role="user", content="compressed")])
    rt.close()
    lines = Path(rt.context.conversation_path).read_text(encoding="utf-8").splitlines()
    records = [json.loads(l) for l in lines]
    assert records[0]["role"] == "user"
    assert records[1]["type"] == "compact"
    assert records[2]["content"] == "compressed"


@pytest.mark.anyio
async def test_runtime_invalid_session_id_rejected(tmp_path):
    """防 bug：非法/越界 session id 不得进入恢复（T1 路径校验）。"""
    rt = SessionRuntime(tmp_path)
    with pytest.raises(ValueError):
        await rt.resume("../../etc/passwd")


# ---------- PromptBuilder 两个 section ----------


def test_builder_sections_priority_and_empty(tmp_path):
    """防 bug：指令 priority 80、记忆 priority 100；空内容不注册（AC24）。"""
    b = PromptBuilder([Section("base", "BASE", 10)])
    b.set_custom_instructions("INSTRUCTIONS")
    b.set_long_term_memory("MEMORY")
    out = b.build()
    assert out.index("BASE") < out.index("INSTRUCTIONS") < out.index("MEMORY")
    # 空内容不增加模块
    b2 = PromptBuilder([Section("base", "B", 10)])
    b2.set_custom_instructions("   ")
    b2.set_long_term_memory("")
    assert b2.build() == "B"


def test_builder_update_replaces_section(tmp_path):
    """防 bug：set_long_term_memory 替换旧模块而非叠加。"""
    b = PromptBuilder([])
    b.set_long_term_memory("v1")
    b.set_long_term_memory("v2")
    out = b.build()
    assert out.count("v2") == 1 and "v1" not in out


# ---------- Agent 自动记忆触发 ----------


class TextProvider:
    """纯文本流（自然 Done，无工具调用）。"""

    def __init__(self, text="ok"):
        self._text = text
        self.model = "mock-model"
        self.name = "mock"

    async def stream(self, payload):
        yield StreamEvent(text=self._text)
        yield StreamEvent(done=True, usage=TokenUsage(1, 1))


class MemoryRecorder:
    """记录记忆更新调用，不真正写盘。"""

    def __init__(self):
        self.calls = []
        self.session_id = "sess-recorder"

    async def update_async(self, messages, session_id=""):
        self.calls.append((list(messages), session_id))
        return []


def _agent(provider, memory=None):
    conv = ConversationManager(20)
    return Agent(
        provider,
        conv,
        Registry(),
        "stable",
        "env",
        memory_manager=memory,
    )


@pytest.mark.anyio
async def test_agent_natural_done_triggers_memory(tmp_path):
    """防 bug：自然完成（无工具）后必须触发记忆更新（F13）。"""
    memory = MemoryRecorder()
    agent = _agent(TextProvider(), memory)
    events = []
    async for e in agent.run("记住 hello"):  # 显式关键词让第 1 轮自然完成后触发
        events.append(e)
    assert events[-1].type == EventType.DONE
    assert events[-1].payload == StopReason.NATURAL
    # 等待 fire-and-forget 的记忆任务
    if agent._memory_task:
        await agent._memory_task
    assert len(memory.calls) == 1
    assert memory.calls[0][1] == "sess-recorder"
    # 快照里包含 user 输入
    assert "记住 hello" in (memory.calls[0][0][0].content or "")


@pytest.mark.anyio
async def test_agent_explicit_keyword_triggers_immediately(tmp_path):
    """防 bug：显式记忆关键词（记住/记忆/remember）立即触发。"""
    memory = MemoryRecorder()
    agent = _agent(TextProvider(), memory)
    async for _ in agent.run("请记住我的偏好是简洁回复"):
        pass
    if agent._memory_task:
        await agent._memory_task
    assert len(memory.calls) == 1


@pytest.mark.anyio
async def test_agent_no_keyword_not_triggered_until_5_rounds(tmp_path):
    """防 bug：无关键词时每 5 个自然完成轮次才触发一次。"""
    memory = MemoryRecorder()
    agent = _agent(TextProvider(), memory)
    for i in range(4):
        async for _ in agent.run(f"question {i}"):
            pass
        if agent._memory_task:
            await agent._memory_task
    assert memory.calls == []
    # 第 5 轮触发
    async for _ in agent.run("question 4"):
        pass
    if agent._memory_task:
        await agent._memory_task
    assert len(memory.calls) == 1


@pytest.mark.anyio
async def test_agent_without_memory_manager_noop(tmp_path):
    """防 bug：未装配 memory_manager 时 Agent 行为与 ch08 完全一致。"""
    agent = _agent(TextProvider(), memory=None)
    events = []
    async for e in agent.run("hi"):
        events.append(e)
    assert events[-1].type == EventType.DONE
    assert agent._memory_task is None


@pytest.mark.anyio
async def test_memory_update_does_not_block_next_input(tmp_path):
    """防 bug：记忆更新未完成时下一条输入立即进入主流程（AC21）。"""
    import time

    class SlowMemory(MemoryRecorder):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def update_async(self, messages, session_id=""):
            self.started.set()
            await self.release.wait()
            self.calls.append(("done", session_id))
            return []

    memory = SlowMemory()
    agent = _agent(TextProvider(), memory)
    async for _ in agent.run("记住 x"):
        pass
    # 记忆任务已启动但未完成
    await memory.started.wait()
    assert not agent._memory_task.done()
    # 下一条输入不等待
    started = time.monotonic()
    async for _ in agent.run("hi again"):
        pass
    elapsed = time.monotonic() - started
    assert elapsed < 1.0  # 没有阻塞
    memory.release.set()
    if agent._memory_task:
        await agent._memory_task
