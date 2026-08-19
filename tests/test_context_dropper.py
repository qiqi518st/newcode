"""MessageGroupDropper 丢消息组单测（ch08 T29，spec F27/F28）。

防 bug：分组拆开 tool_use/tool_result 对、丢弃数计算错误、空列表异常。
"""

from mewcode.context.dropper import MessageGroupDropper
from mewcode.provider.base import Message


def _conv():
    """构造：u1→a→tool, u2→a→tool, u3→a（3 组）。"""
    return [
        Message(role="user", content="u1"),
        Message(role="assistant", content="a1", tool_calls=[{"id": "t1"}]),
        Message(role="tool", content="r1", tool_use_id="t1"),
        Message(role="user", content="u2"),
        Message(role="assistant", content="a2", tool_calls=[{"id": "t2"}]),
        Message(role="tool", content="r2", tool_use_id="t2"),
        Message(role="user", content="u3"),
        Message(role="assistant", content="a3"),
    ]


def test_group_by_user_boundary():
    """防 bug：连续 user 各自成组、非 user 消息归入当前组。

    含连续 user 时每组以 user 开头。
    """
    msgs = [Message(role="user", content="u1"), Message(role="user", content="u2")]
    groups = MessageGroupDropper.group_by_user(msgs)
    assert len(groups) == 2
    assert groups[0][0].content == "u1"
    assert groups[1][0].content == "u2"


def test_group_keeps_pair():
    """防 bug：分组拆开 tool_use 与 tool_result 对 → Anthropic API 报 tool_call_ids 不匹配。

    user→assistant(tool_use)→tool 应整组保留，tool 不与 assistant 分离。
    """
    groups = MessageGroupDropper.group_by_user(_conv())
    assert len(groups) == 3
    # 第 1 组含 u1/a1/r1，tool_use 与 tool_result 在同组
    assert any(m.role == "tool" and m.tool_use_id == "t1" for m in groups[0])
    assert any(m.role == "assistant" and m.tool_calls for m in groups[0])


def test_group_leading_non_user():
    """防 bug：首条非 user 消息丢失（无前置 user 组）。

    第一条是 assistant/tool 时应单独成组，不丢。
    """
    msgs = [Message(role="assistant", content="a0"), Message(role="user", content="u1")]
    groups = MessageGroupDropper.group_by_user(msgs)
    assert len(groups) == 2
    assert groups[0][0].role == "assistant"


def test_drop_oldest():
    """防 bug：drop_oldest 丢错端（丢了最新而非最旧）。"""
    groups = MessageGroupDropper.group_by_user(_conv())
    kept = MessageGroupDropper.drop_oldest(groups, 1)
    assert len(kept) == 2
    # 丢的是最旧的 u1 组
    assert kept[0][0].content == "u2"


def test_drop_ratio_min_one():
    """防 bug：drop_ratio 在小列表时丢 0 组 → 死循环。

    空/1 组/多组三分支，drop 量 ≥ 1。
    """
    assert MessageGroupDropper.drop_ratio([], 0.2) == []
    one = [[Message(role="user", content="u")]]
    assert MessageGroupDropper.drop_ratio(one, 0.2) == []  # 1 组丢 1 → 空
    groups = MessageGroupDropper.group_by_user(_conv())
    kept = MessageGroupDropper.drop_ratio(groups, 0.2)  # ceil(3*0.2)=1
    assert len(kept) == 2


def test_drop_all_returns_empty():
    """防 bug：全部丢弃未返回空列表 → 返回残留。"""
    groups = MessageGroupDropper.group_by_user(_conv())
    assert MessageGroupDropper.drop_oldest(groups, 3) == []
    assert MessageGroupDropper.drop_oldest(groups, 10) == []
