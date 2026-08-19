"""ConversationManager 工具消息测试"""

import pytest

from mewcode.conversation.manager import ConversationManager
from mewcode.provider.base import ToolCall, ToolResult


@pytest.mark.anyio
async def test_tool_message_sequence():
    cm = ConversationManager(20)
    cm.add_user("read main.py")
    tc = ToolCall("read_file", {"path": "main.py"}, tool_call_id="tc_1")
    cm.add_tool_call(tc)
    cm.add_tool_result(tc, ToolResult("ok", "import os"))
    cm.add_assistant("main.py 的第一行是 import os")

    msgs = cm.get_context()
    roles = [m.role for m in msgs]
    assert roles == ["user", "assistant", "tool", "assistant"]


@pytest.mark.anyio
async def test_tool_message_ids():
    cm = ConversationManager(20)
    tc = ToolCall("read_file", {}, tool_call_id="tc_abc", tool_use_id="tu_abc")
    cm.add_tool_call(tc)
    cm.add_tool_result(tc, ToolResult("ok", "content"))

    msgs = cm.get_context()
    tool_msg = next(m for m in msgs if m.role == "tool")
    assert tool_msg.tool_call_id == "tc_abc"
    assert tool_msg.name == "read_file"


@pytest.mark.anyio
async def test_trim_with_tool_messages():
    """ch08 语义变更：_trim 从「整对裁剪到 max_turns 对」放宽为「按 user 分组、
    cap=max_turns*2 组、仅降级兜底」（主裁剪权已交 context）。本测试验证核心保证：
    触发裁剪后 tool_use/tool_result 对不拆、组数不超过 cap。"""
    cm = ConversationManager(2)  # cap = 2*2 = 4 组
    # 塞满 4 组纯文本（等于 cap，不触发）
    for i in range(4):
        cm.add_user(f"q{i}")
        cm.add_assistant(f"a{i}")

    # 加一组 tool 消息
    cm.add_user("q4")
    tc = ToolCall("t", {})
    cm.add_tool_call(tc)
    cm.add_tool_result(tc, ToolResult("ok", "r"))
    cm.add_assistant("a4")

    # 再塞 1 组纯文本 → 6 组 > cap(4)，触发裁剪
    cm.add_user("q5")
    cm.add_assistant("a5")

    msgs = cm.get_context()
    # 裁剪后：无 tool 落单（tool 前必有带 tool_calls 的 assistant；id 一致由
    # test_conversation_manager.py::test_trim_keeps_pair 用 tool_use_id 覆盖）
    for i, m in enumerate(msgs):
        if m.role == "tool":
            assert (
                i > 0 and msgs[i - 1].role == "assistant" and msgs[i - 1].tool_calls
            ), f"tool 落单 at {i}"
    # 组数不超过 cap（4 组）
    user_count = sum(1 for m in msgs if m.role == "user")
    assert user_count <= 4, f"裁剪后 user 组数应 ≤ 4，实际 {user_count}"
