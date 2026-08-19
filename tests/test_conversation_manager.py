"""ConversationManager ch08 改造单测（T33，spec F15/_trim 不拆对）。

防 bug：get_messages_ref 返回内部引用、replace_history 隔离、_trim 拆 tool 对致 API 报错。
"""

from mewcode.conversation.manager import ConversationManager
from mewcode.provider.base import Message, ToolCall, ToolResult


def test_get_messages_ref_same_object():
    """防 bug：get_messages_ref 返回副本 → offload_and_snip 改写不生效。

    应返回内部 _messages 原始引用（is 断言），且就地改动反映到 get_context() 副本。
    """
    cm = ConversationManager(20)
    cm.add_user("hi")
    ref = cm.get_messages_ref()
    ctx = cm.get_context()
    assert ref is cm.get_messages_ref()  # 同一对象
    assert ctx is not ref  # get_context 返回副本
    # 就地改写 content 应反映
    ref[0].content = "TAMPERED"
    assert cm.get_context()[0].content == "TAMPERED"


def test_replace_history_replaces():
    """防 bug：replace_history 未隔离 → 旧列表外部持有影响新列表。

    replace_history 应设为新列表的副本，外部持有旧列表不影响。
    """
    cm = ConversationManager(20)
    cm.add_user("old")
    old_msgs = cm.get_messages_ref()
    new = [Message(role="user", content="new"), Message(role="assistant", content="a")]
    cm.replace_history(new)
    assert cm.get_context() == new
    # 外部改动 old_msgs 不影响新历史
    old_msgs.append(Message(role="user", content="inject"))
    assert len(cm.get_context()) == 2, "旧列表外部改动不应影响"


def test_replace_history_copies_input():
    """防 bug：replace_history 直接持有传入列表 → 外部改动污染。"""
    cm = ConversationManager(20)
    new = [Message(role="user", content="x")]
    cm.replace_history(new)
    new.append(Message(role="assistant", content="y"))
    assert len(cm.get_context()) == 1, "传入列表外部改动不应影响"


def test_trim_keeps_pair():
    """防 bug：_trim 整对丢弃拆 tool_use/tool_result 对 → Anthropic API 报 tool_call_ids 不匹配。

    ch08 _trim 按 user 分组整组丢，tool 对不拆。
    """
    cm = ConversationManager(2)
    # 1 个工具轮
    cm.add_user("用工具")
    tc = ToolCall("read_file", {"path": "f"}, tool_use_id="t0")
    cm.add_assistant_with_tool_calls("", [tc])
    cm.add_tool_result(tc, ToolResult("ok", "content"))
    # 大量纯文本轮触发 _trim（add_assistant 触发）
    for k in range(10):
        cm.add_user(f"纯文本{k}")
        cm.add_assistant(f"答复{k}")

    msgs = cm.get_context()
    # 无 tool 落单：每个 tool 消息前必有带 tool_calls 的 assistant，id 一致
    for i, m in enumerate(msgs):
        if m.role == "tool":
            assert (
                i > 0 and msgs[i - 1].role == "assistant" and msgs[i - 1].tool_calls
            ), f"tool 落单 at {i}"
            assert msgs[i - 1].tool_calls[0]["id"] == m.tool_use_id, "配对 id 一致"


def test_trim_group_cap():
    """防 bug：_trim 触发阈值过严 → 每轮都裁、context 主裁剪权失效。

    ch08 _trim 放宽到 max_turns * 2 组才触发（降级兜底）。
    """
    cm = ConversationManager(3)
    # 5 轮纯文本（5 组）≤ 3*2=6，不应触发 _trim
    for k in range(5):
        cm.add_user(f"q{k}")
        cm.add_assistant(f"a{k}")
    msgs = cm.get_context()
    user_count = sum(1 for m in msgs if m.role == "user")
    assert user_count == 5, "5 组 ≤ cap=6 不应裁剪"
    # 第 7 组触发裁剪
    cm.add_user("q6")
    cm.add_assistant("a6")
    cm.add_user("q7")
    cm.add_assistant("a7")
    msgs = cm.get_context()
    user_count = sum(1 for m in msgs if m.role == "user")
    assert user_count <= 6, "超过 cap 应裁剪到 ≤6 组"
