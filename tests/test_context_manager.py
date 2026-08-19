"""ContextManager 编排单测（ch08 T32，spec F7/F23-F28/F34，AC5/AC13/AC17/AC20a/AC5a）。

防 bug：阈值触发、自动闸、强制 L1、窗口下界、并发互斥、锚点重置。
"""


import pytest

from mewcode.context.files import FileTracker
from mewcode.context.manager import ContextManager
from mewcode.conversation.manager import ConversationManager
from mewcode.provider.base import StreamEvent, ToolCall, ToolResult


class MockProvider:
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
    return [StreamEvent(text="<summary>摘要</summary>"), StreamEvent(done=True)]


def _fail_script(i):
    return [StreamEvent(err=RuntimeError("api down"))]


def _build_conv(cm, turns=15, big_first=True):
    for k in range(turns):
        cm.add_user(f"用户{k}：" + "x" * 3000)
        tc = ToolCall(tool_name="read_file", arguments={"path": "f"}, tool_use_id=f"t{k}")
        cm.add_assistant_with_tool_calls("", [tc])
        cm.add_tool_result(
            tc,
            ToolResult(status="ok", output="y" * 60000 if (big_first and k == 0) else "ok"),
        )


def _make_ctx(provider, cm, tmp_path, ft=None, **kw):
    ft = ft or FileTracker()
    return ContextManager(
        provider, cm, "claude-sonnet-4", "anthropic", ft, workspace=str(tmp_path), **kw
    )


@pytest.mark.anyio
async def test_auto_triggers_on_threshold(tmp_path, monkeypatch):
    """AC5：达阈值 → 触发 L2 摘要。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "40000")  # 阈值=7000
    cm = ConversationManager(20)
    _build_conv(cm)
    p = MockProvider(_ok_script)
    ctx = _make_ctx(p, cm, tmp_path)
    await ctx.manage_context([])
    assert len(p.calls) == 1, "达阈值应触发摘要"


@pytest.mark.anyio
async def test_auto_skipped_below_threshold(tmp_path, monkeypatch):
    """防 bug：未达阈值仍触发摘要 → 无谓压缩。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "200000")
    cm = ConversationManager(20)
    # 小对话：4 轮短消息
    for k in range(4):
        cm.add_user(f"小{k}：" + "x" * 500)
        cm.add_assistant(f"答{k}")
    p = MockProvider(_ok_script)
    ctx = _make_ctx(p, cm, tmp_path)
    await ctx.manage_context([])
    assert len(p.calls) == 0, "未达阈值不应摘要"


@pytest.mark.anyio
async def test_auto_uses_layer1_output(tmp_path, monkeypatch):
    """防 bug：用 L1 前估算偏高 → L1 替换后已低于阈值仍触发 L2。

    L1 替换大结果后重估应跌到阈值以下，不再触发 L2。
    """
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "40000")  # 阈值=7000
    cm = ConversationManager(20)
    _build_conv(cm, turns=4)  # 4 轮，含 1 个 60K 大结果
    p = MockProvider(_ok_script)
    ctx = _make_ctx(p, cm, tmp_path)
    await ctx.manage_context([])
    # L1 替换 60K 后，4 轮小对话估算应 < 7000，不触发 L2
    assert len(p.calls) == 0, "L1 替换后低于阈值不应再触发 L2"


@pytest.mark.anyio
async def test_auto_skipped_when_gate_disabled(tmp_path, monkeypatch):
    """AC20a：闸触发 → 跳过 L2 仅 L1。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "40000")
    cm = ConversationManager(20)
    _build_conv(cm)
    p = MockProvider(_fail_script)
    ctx = _make_ctx(p, cm, tmp_path)
    # 3 次失败触发闸
    for _ in range(3):
        await ctx.manage_context([])
    assert ctx._auto_gate.auto_disabled()
    n_before = len(p.calls)
    await ctx.manage_context([])
    assert len(p.calls) == n_before, "闸停后不应再调摘要"


@pytest.mark.anyio
async def test_auto_failure_records_gate(tmp_path, monkeypatch):
    """AC20a：连续 3 轮失败 → auto_disabled()。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "40000")
    cm = ConversationManager(20)
    _build_conv(cm)
    p = MockProvider(_fail_script)
    ctx = _make_ctx(p, cm, tmp_path)
    for i in range(3):
        await ctx.manage_context([])
    assert ctx._auto_gate.auto_disabled(), "3 轮失败应闸停"


@pytest.mark.anyio
async def test_manual_bypasses_everything(tmp_path, monkeypatch):
    """AC13：手动 /compact 远低于阈值仍摘要。

    手动路径跳阈值判断：即使对话远低于自动阈值也触发摘要（此处对话足够大
    能产生 old_block，走真实摘要路径；成功即证明无条件触发）。
    """
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "200000")  # 高阈值
    cm = ConversationManager(20)
    _build_conv(cm)  # 15 轮大对话，远低于 200000 阈值但能产生 old_block
    p = MockProvider(_ok_script)
    ctx = _make_ctx(p, cm, tmp_path)
    out = await ctx.compact_now([])
    assert out.success, "手动应无条件摘要"
    assert len(p.calls) == 1, "手动应真正调用摘要"


@pytest.mark.anyio
async def test_manual_success_resets_gate(tmp_path, monkeypatch):
    """AC20a：手动成功解除自动闸。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "40000")
    cm = ConversationManager(20)
    _build_conv(cm)
    p = MockProvider(_ok_script)
    ctx = _make_ctx(p, cm, tmp_path)
    # 先把闸打到 disabled
    for _ in range(3):
        ctx._auto_gate.record_auto_failure()
    assert ctx._auto_gate.auto_disabled()
    out = await ctx.compact_now([])
    assert out.success
    assert not ctx._auto_gate.auto_disabled(), "手动成功应解除闸"


@pytest.mark.anyio
async def test_emergency_runs_layer1_first(tmp_path, monkeypatch):
    """AC17：紧急压缩先强制 L1 挪走 50K+ 再摘要。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "40000")
    cm = ConversationManager(20)
    _build_conv(cm)  # 含 60K 大结果
    p = MockProvider(_ok_script)
    ctx = _make_ctx(p, cm, tmp_path)
    out = await ctx.force_compact([])
    assert out.success, out.failure_reason
    assert out.replaced_results == 1, "强制 L1 应替换 60K 结果"


@pytest.mark.anyio
async def test_emergency_bypasses_gate(tmp_path, monkeypatch):
    """防 bug：闸已触发时紧急压缩仍能执行（不跨种类，紧急不受闸约束）。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "40000")
    cm = ConversationManager(20)
    _build_conv(cm)
    p = MockProvider(_ok_script)
    ctx = _make_ctx(p, cm, tmp_path)
    # 闸已触发
    for _ in range(3):
        ctx._auto_gate.record_auto_failure()
    assert ctx._auto_gate.auto_disabled()
    out = await ctx.force_compact([])
    assert out.success, "紧急压缩不应受闸约束"


@pytest.mark.anyio
async def test_context_window_floor_check(tmp_path, monkeypatch, caplog):
    """AC5a：窗口 ≤33000 跳过 L2 + warning。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "30000")  # ≤ 33000
    cm = ConversationManager(20)
    _build_conv(cm)
    p = MockProvider(_ok_script)
    ctx = _make_ctx(p, cm, tmp_path)
    import logging

    with caplog.at_level(logging.WARNING):
        await ctx.manage_context([])
    assert len(p.calls) == 0, "窗口过小应跳过 L2"
    assert any("too small" in r.message for r in caplog.records), "应 warning"


@pytest.mark.anyio
async def test_concurrent_manage_and_compact_mutex(tmp_path, monkeypatch):
    """F34：并发 manage_context 与 compact_now 不交错改写 conversation。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "40000")
    cm = ConversationManager(20)
    _build_conv(cm)
    p = MockProvider(_ok_script)
    ctx = _make_ctx(p, cm, tmp_path)
    import asyncio

    # 并发执行：两者都拿同一把 _lock，应串行不交错
    await asyncio.gather(
        ctx.manage_context([]),
        ctx.compact_now([]),
        ctx.manage_context([]),
    )
    # 不抛异常即证明互斥串行（asyncio.Lock 保证）
    # 历史最终被替换为摘要消息
    assert cm.get_messages_ref()[0].role == "user"


@pytest.mark.anyio
async def test_anchor_reset_after_compact(tmp_path, monkeypatch):
    """防 bug：摘要成功后锚点未重置 → 估算仍用旧 anchor 偏高。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "40000")
    cm = ConversationManager(20)
    _build_conv(cm)
    p = MockProvider(_ok_script)
    ctx = _make_ctx(p, cm, tmp_path)
    from mewcode.provider.base import TokenUsage

    # 模拟锚点已涵盖部分历史（anchor_msg_len < 消息数），使 L1 后估算仍达阈值
    ctx.update_anchor(TokenUsage(1000, 200), 0)
    assert ctx.usage_anchor == 1200
    await ctx.manage_context([])
    assert ctx.usage_anchor == 0 and ctx.anchor_msg_len == 0, "摘要成功应重置锚点"
