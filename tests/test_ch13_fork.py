"""ch13 subagent/fork.py Fork 消息装填测试。

防的 bug：
- 深拷贝缺失 → 改子 conv 污染父对话（Fork 的隔离核心）
- 悬空 tool_use 不补 placeholder → API 报 tool_call_ids 配对错误
- Boilerplate 不写在任务前 → Fork 子 Agent 首条消息失去约束（再 Fork/对话/请求确认）
- is_fork_context 漏扫 user 消息 → Fork 嵌套兜底失效（B2 层 1）
"""

from __future__ import annotations

from mewcode.conversation.manager import ConversationManager
from mewcode.provider.base import ToolCall, ToolResult
from mewcode.subagent.fork import (
    FORK_BOILERPLATE_TAG,
    build_forked_messages,
    is_fork_context,
)


def test_empty_parent_single_boilerplate_user():
    msgs = build_forked_messages(ConversationManager(20), "hello task")
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content.startswith(FORK_BOILERPLATE_TAG)
    assert msgs[0].content.endswith("hello task")
    assert is_fork_context(msgs) is True


def test_complete_history_appends_one_user():
    conv = ConversationManager(20)
    conv.add_user("u1")
    conv.add_assistant("a1")
    tc = ToolCall(tool_name="read_file", arguments={"path": "/x"}, tool_use_id="tu1")
    conv.add_assistant_with_tool_calls("", [tc])
    conv.add_tool_result(tc, ToolResult(status="ok", output="data"))
    msgs = build_forked_messages(conv, "t2")
    assert len(msgs) == len(conv.get_context()) + 1
    assert msgs[-1].role == "user" and msgs[-1].content.endswith("t2")


def test_dangling_tool_use_gets_placeholder():
    conv = ConversationManager(20)
    conv.add_user("u")
    conv.add_assistant_with_tool_calls(
        "",
        [ToolCall(tool_name="read_file", arguments={"path": "/a"}, tool_use_id="d1")],
    )
    msgs = build_forked_messages(conv, "t3")
    # 原 2 条 + placeholder 1 + user 1 = 4
    assert len(msgs) == 4
    ph = msgs[2]
    assert ph.role == "tool" and ph.tool_use_id == "d1"
    assert ph.content == "（继承上下文，未完成）"


def test_deep_copy_isolation():
    conv = ConversationManager(20)
    conv.add_user("u")
    conv.add_assistant_with_tool_calls(
        "",
        [ToolCall(tool_name="read_file", arguments={"path": "/a"}, tool_use_id="d1")],
    )
    msgs = build_forked_messages(conv, "t")
    msgs[0].content = "mutated"
    msgs[1].tool_calls[0]["arguments"]["path"] = "mutated"  # type: ignore[index]
    assert conv.get_context()[0].content == "u"
    assert conv.get_context()[1].tool_calls[0]["arguments"]["path"] == "/a"


def test_is_fork_context_negative():
    conv = ConversationManager(20)
    conv.add_user("plain")
    assert is_fork_context(conv.get_context()) is False
