"""ConversationManager 工具消息测试"""

import pytest
from mewcode.conversation.manager import ConversationManager
from mewcode.provider.base import ToolCall, ToolResult


@pytest.mark.anyio
async def test_tool_message_sequence():
    cm = ConversationManager("", 20)
    cm.add_user("read main.py")
    cm.add_tool_call(ToolCall("read_file", {"path": "main.py"}, tool_call_id="tc_1"))
    cm.add_tool_result(ToolResult("ok", "import os"))
    cm.add_assistant("main.py 的第一行是 import os")

    msgs = cm.get_context()
    roles = [m.role for m in msgs]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]


@pytest.mark.anyio
async def test_tool_message_ids():
    cm = ConversationManager("", 20)
    cm.add_tool_call(ToolCall("read_file", {}, tool_call_id="tc_abc", tool_use_id="tu_abc"))
    cm.add_tool_result(ToolResult("ok", "content"))

    msgs = cm.get_context()
    tool_msg = [m for m in msgs if m.role == "tool"][0]
    assert tool_msg.tool_call_id == "tc_abc"
    assert tool_msg.name == "read_file"


@pytest.mark.anyio
async def test_trim_with_tool_messages():
    cm = ConversationManager("", 2)
    # 塞满 3 对 user/assistant（超过 max_turns=2）
    for i in range(3):
        cm.add_user(f"q{i}")
        cm.add_assistant(f"a{i}")

    # 加一组 tool 消息
    cm.add_user("q3")
    cm.add_tool_call(ToolCall("t", {}))
    cm.add_tool_result(ToolResult("ok", "r"))
    cm.add_assistant("a3")

    msgs = cm.get_context()
    # 滑动窗口应保留最近 2 对
    roles = [m.role for m in msgs]
    # 由于 _trim 在 add_assistant 时触发，tool 消息不参与对数计算
    # 但不会被误删
    assert len(msgs) <= 1 + 2 * 2 + 3  # system + 2 对 + 可能的 tool 消息
