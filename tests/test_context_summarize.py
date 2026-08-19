"""Summarizer 第二层摘要单测（ch08 T31，spec F8/F9/F11/F12/F15/F27，AC6-AC8a）。

防 bug：摘要不传工具、只留 summary、合并单条 user、role 衔接、双下界、PTL 丢组、锚点污染。
"""

import pytest

from mewcode.context.constants import RECENT_COUNT_FLOOR, RECENT_TOKEN_FLOOR
from mewcode.context.recovery import RecoveryBuilder
from mewcode.context.files import FileTracker
from mewcode.context.summarize import (
    SummarizeConfig,
    Summarizer,
    _join_after_summary,
    extract_summary,
    pick_recent_tail,
)
from mewcode.context.tokens import estimate_messages
from mewcode.llm import PromptTooLongError
from mewcode.provider.base import Message, StreamEvent, TokenUsage, ToolDefinition


class MockProvider:
    """记录每次请求 payload，按 script 产出 StreamEvent。"""

    name = "mock"
    model = "mock-model"

    def __init__(self, script=None):
        self.calls: list = []
        self.script = script or (lambda i: [])

    async def stream(self, payload):
        self.calls.append(payload)
        i = len(self.calls) - 1
        for se in self.script(i):
            yield se


def _ok_script(i):
    return [
        StreamEvent(text="<analysis>草稿</analysis>\n<summary>正式摘要</summary>"),
        StreamEvent(done=True),
    ]


def _build_large_conv(turns=20):
    """构造足够大对话：每轮 user(3000 字) + assistant(tool) + tool 结果。"""
    msgs = []
    for k in range(turns):
        msgs.append(Message(role="user", content=f"用户{k}：" + "x" * 3000))
        msgs.append(
            Message(
                role="assistant",
                content="",
                tool_calls=[{"id": f"t{k}", "name": "read_file", "arguments": {}}],
            )
        )
        msgs.append(Message(role="tool", content="结果" * 500, tool_use_id=f"t{k}"))
    return msgs


@pytest.mark.anyio
async def test_summary_request_no_tools():
    """AC6：摘要请求不传 tools（payload.tools is None）。"""
    ft = FileTracker()
    p = MockProvider(_ok_script)
    s = Summarizer(p, RecoveryBuilder(), ft)
    await s.summarize(_build_large_conv(), SummarizeConfig(3000, 6), 200_000, [])
    assert len(p.calls) == 1
    assert p.calls[0].tools is None


@pytest.mark.anyio
async def test_extract_summary_only():
    """AC7：只留 <summary> 正文，草稿丢弃。"""
    raw = "<analysis>草稿内容</analysis>\n<summary>九段正式摘要\n6. 用户原文</summary>"
    assert extract_summary(raw) == "九段正式摘要\n6. 用户原文"
    assert "草稿内容" not in extract_summary(raw)


@pytest.mark.anyio
async def test_merge_single_user_message():
    """AC8a：摘要+恢复合并单条 user、全程无连续 user。"""
    ft = FileTracker()
    p = MockProvider(_ok_script)
    s = Summarizer(p, RecoveryBuilder(), ft)
    out = await s.summarize(_build_large_conv(), SummarizeConfig(3000, 6), 200_000, [])
    assert out.success and out.messages
    roles = [m.role for m in out.messages]
    # 首条是 user（摘要+恢复合并）
    assert roles[0] == "user"
    assert "正式摘要" in out.messages[0].content
    # 全程无连续 user
    for i in range(1, len(roles)):
        assert not (roles[i] == "user" and roles[i - 1] == "user"), f"连续 user at {i}: {roles}"


def test_role_join_placeholder():
    """AC8a：近期原文首条 user → 插 assistant 占位衔接。"""
    summary = Message(role="user", content="摘要")
    recent = [Message(role="user", content="u1"), Message(role="assistant", content="a1")]
    joined = _join_after_summary(summary, recent)
    roles = [m.role for m in joined]
    assert roles == ["user", "assistant", "user", "assistant"], roles
    assert joined[1].content  # 占位非空


def test_role_join_assistant_first():
    """AC8a：近期原文首条 assistant → 无需占位，直接拼接。"""
    summary = Message(role="user", content="摘要")
    recent = [Message(role="assistant", content="a1")]
    joined = _join_after_summary(summary, recent)
    assert [m.role for m in joined] == ["user", "assistant"]


def test_role_join_empty_recent():
    """AC8a：近期原文空 → 只有摘要消息。"""
    summary = Message(role="user", content="摘要")
    assert _join_after_summary(summary, []) == [summary]


def test_recent_tail_dual_floor():
    """AC8：双下界（token ≥ RECENT_TOKEN_FLOOR 且 条数 ≥ RECENT_COUNT_FLOOR）。"""
    # 40 条 user 各 1000 字符（~285 token/条）：需 ≥ 35 条才达 10000 token
    msgs = [Message(role="user", content="x" * 1000) for _ in range(40)]
    tail = pick_recent_tail(msgs)
    assert len(tail) >= RECENT_COUNT_FLOOR
    assert estimate_messages(tail) >= RECENT_TOKEN_FLOOR
    # 首条不落单 tool
    assert tail[0].role != "tool"


def test_recent_tail_keeps_pair():
    """AC8 子：截断点不拆 tool_use/tool_result 对（F12）。"""
    msgs = [
        Message(role="user", content="q"),
        Message(role="assistant", content="", tool_calls=[{"id": "t1"}]),
        Message(role="tool", content="R" * 6000, tool_use_id="t1"),
    ]
    # 加足够多尾部 user 消息使截断点落在 tool 附近
    for k in range(40):
        msgs.append(Message(role="user", content="z" * 900))
    tail = pick_recent_tail(msgs)
    # 首条不应是落单 tool
    assert tail[0].role != "tool"
    if any(m.role == "tool" for m in tail):
        # 若含 tool，其前必有带 tool_calls 的 assistant
        for i, m in enumerate(tail):
            if m.role == "tool":
                assert tail[i - 1].role == "assistant" and tail[i - 1].tool_calls


@pytest.mark.anyio
async def test_ptl_retry_drops_groups():
    """F27：前 3 次每次丢 1 组、之后比例丢弃，防「不丢组直接撞墙」。"""
    ft = FileTracker()
    # 前 5 次都撞 PTL，第 6 次成功
    def script(i):
        if i < 5:
            return [StreamEvent(err=PromptTooLongError("too long"))]
        return _ok_script(i)

    p = MockProvider(script)
    s = Summarizer(p, RecoveryBuilder(), ft)
    out = await s.summarize(_build_large_conv(), SummarizeConfig(3000, 6), 200_000, [])
    assert out.success, out.failure_reason
    # 应有多次调用（初始 + 3 次直接重试 + 比例丢弃重试）
    assert len(p.calls) > 1, "PTL 应触发丢组重试，而非单次撞墙"


@pytest.mark.anyio
async def test_ptl_retry_stops_before_empty():
    """F27：全丢光仍失败 → 失败 outcome，不发送空 messages 摘要请求。"""
    ft = FileTracker()
    # 始终撞 PTL
    p = MockProvider(lambda i: [StreamEvent(err=PromptTooLongError("always too long"))])
    s = Summarizer(p, RecoveryBuilder(), ft)
    out = await s.summarize(_build_large_conv(), SummarizeConfig(3000, 6), 200_000, [])
    assert not out.success
    assert out.failure_reason == "prompt_too_long"
    # 不应发送空 messages 请求：每次调用的 messages 应非空
    for call in p.calls:
        assert len(call.messages) > 0, "不应发送空 messages 摘要请求"


@pytest.mark.anyio
async def test_summary_does_not_update_anchor():
    """防 bug：摘要路径调用 update_anchor → 摘要 usage 污染主对话锚点。

    Summarizer 无 update_anchor 方法，锚点只由 ContextManager/Agent 主对话路径维护。
    """
    ft = FileTracker()
    p = MockProvider(_ok_script)
    s = Summarizer(p, RecoveryBuilder(), ft)
    # Summarizer 不应暴露 update_anchor
    assert not hasattr(s, "update_anchor"), "Summarizer 不应维护锚点"
    await s.summarize(_build_large_conv(), SummarizeConfig(3000, 6), 200_000, [])
    # provider 被调用，但 Summarizer 自身未触碰锚点（ContextManager 持锚点）


@pytest.mark.anyio
async def test_max_output_tokens_passthrough():
    """防 bug：摘要请求 max_output_tokens=8192 未透传 → 九段摘要被 4096 截断。"""
    ft = FileTracker()
    p = MockProvider(_ok_script)
    s = Summarizer(p, RecoveryBuilder(), ft)
    await s.summarize(_build_large_conv(), SummarizeConfig(3000, 6), 200_000, [])
    assert p.calls[0].max_output_tokens == 8192
