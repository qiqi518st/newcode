"""token 估算纯函数单测（ch08 T27，spec F13/F14）。

每个测试 docstring 注明防的 bug，防后人误删关键用例。
"""

from types import SimpleNamespace

from mewcode.context.tokens import (
    estimate_messages,
    estimate_tokens,
    message_chars,
    usage_to_anchor,
)
from mewcode.provider.base import Message


def _usage(inp=0, out=0, cc=0, cr=0) -> SimpleNamespace:
    """构造类 TokenUsage 对象（SimpleNamespace mock，仿 test_cache_usage 范式）。"""
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_creation_input_tokens=cc,
        cache_read_input_tokens=cr,
    )


def test_usage_anchor_sum():
    """防 bug：锚点漏算 cache 字段导致估算偏低、过早撞墙。

    usage_to_anchor 必须四字段求和（input+output+cache_creation+cache_read）。
    """
    u = _usage(inp=1000, out=500, cc=200, cr=300)
    assert usage_to_anchor(u) == 2000


def test_estimate_tokens_delta_only():
    """防 bug：重复计算已含进 anchor 的历史 → 估算翻倍、过早触发 L2。

    anchor=1000 已涵盖 all_msgs[:1]，估算应只加 all_msgs[1:] 的字符增量。
    """
    m1 = Message(role="user", content="x" * 350)  # 100 token
    m2 = Message(role="assistant", content="y" * 350)  # 100 token
    est = estimate_tokens(anchor=1000, all_msgs=[m1, m2], anchor_msg_len=1)
    # 只算 m2：1000 + ceil(350/3.5) = 1000 + 100
    assert est == 1100


def test_estimate_tokens_zero_anchor():
    """防 bug：anchor=0 退化时应纯字符估算，不应输出 0。"""
    msgs = [Message(role="user", content="x" * 350)]
    est = estimate_tokens(anchor=0, all_msgs=msgs, anchor_msg_len=0)
    assert est == 100  # ceil(350/3.5)


def test_estimate_tokens_anchor_msg_len_clamp():
    """防 bug：anchor_msg_len 超出消息数时负索引误算全量。

    max(0, anchor_msg_len) 保护；超出消息数时 tail 为空，只返回 anchor。
    """
    msgs = [Message(role="user", content="x" * 350)]
    est = estimate_tokens(anchor=500, all_msgs=msgs, anchor_msg_len=10)
    # tail = msgs[10:] = [] → 只返回 anchor
    assert est == 500


def test_estimate_messages_chars_only():
    """防 bug：estimate_messages 纯字符/3.5，不锚定 usage。"""
    msgs = [Message(role="user", content="x" * 700)]
    assert estimate_messages(msgs) == 200  # ceil(700/3.5)


def test_message_chars_includes_tool_calls():
    """防 bug：tool_calls 序列化字节漏算 → tool_use 声明不计入估算。"""
    m = Message(
        role="assistant",
        content="hi",
        tool_calls=[{"id": "t1", "name": "r", "arguments": {"p": "x"}}],
    )
    plain = len(b"hi")
    assert message_chars([m]) > plain


def test_estimate_tokens_large_no_overflow():
    """防 bug：大 anchor 值（2B）整数溢出或误解为负。

    Python int 无上限，但防未来改用固定宽度类型时的回归。
    """
    msgs = [Message(role="user", content="x" * 350)]
    est = estimate_tokens(anchor=2_000_000_000, all_msgs=msgs, anchor_msg_len=0)
    assert est == 2_000_000_100
    assert est > 0
