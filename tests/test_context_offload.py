"""第一层 offload 单测（ch08 T30，spec F1/F2/F2a/F3/F4/F5，AC1-AC4）。

防 bug：单条/聚合阈值、幂等、决策冻结、落盘失败回滚、三步原子。
"""

import os

import pytest

from mewcode.context.constants import (
    PREVIEW_MAX_BYTES,
    PREVIEW_MAX_LINES,
)
from mewcode.context.offload import _head_preview, build_preview, offload_and_snip
from mewcode.context.replacement import ContentReplacementState
from mewcode.context.session import SessionContext, SessionPaths
from mewcode.provider.base import Message


def _setup(tmp_path):
    sc = SessionContext(session_id="t", spill_dir=str(tmp_path / "spill"))
    sp = SessionPaths(sc)
    return sc, sp


@pytest.mark.anyio
async def test_single_result_offload(tmp_path):
    """AC1：60000 字节超 SINGLE_RESULT_THRESHOLD → 替换为预览、文件落盘、预览含四项信息。"""
    _sc, sp = _setup(tmp_path)
    state = ContentReplacementState()
    msgs = [
        Message(role="assistant", content="", tool_calls=[{"id": "t1"}]),
        Message(role="tool", content="X" * 60_000, tool_use_id="t1"),
    ]
    n = await offload_and_snip(msgs, state, sp)
    assert n == 1
    content = msgs[1].content
    assert "[content offloaded]" in content
    assert "original size: 60000 bytes" in content
    assert "[saved to]" in content
    assert "[head preview]" in content
    # 文件落盘
    assert (tmp_path / "spill" / "t1").exists()
    with open(tmp_path / "spill" / "t1", "rb") as f:  # noqa: ASYNC230
        assert f.read() == b"X" * 60_000


@pytest.mark.anyio
async def test_head_preview_limits():
    """AC1 子：头部预览 ≤20 行且 ≤2048 字节。"""
    # 行数限制
    long_lines = "\n".join(f"line{i}" for i in range(100))
    head = _head_preview(long_lines)
    assert head.count("\n") <= PREVIEW_MAX_LINES
    # 字节限制
    big = "字" * 5000  # 中文字节多
    head = _head_preview(big)
    assert len(head.encode("utf-8")) <= PREVIEW_MAX_BYTES


@pytest.mark.anyio
async def test_aggregate_offload(tmp_path):
    """AC2：多条中量结果聚合超 AGGREGATE_LIMIT → 按大→小落盘直到聚合 ≤ 阈值。

    5 条各 45000（均 ≤ SINGLE_RESULT_THRESHOLD，走 F2 聚合分支）= 225000 > 200000，
    落 1 条（45000）后 180000 ≤ 200000 → 替换数=1。
    """
    _sc, sp = _setup(tmp_path)
    state = ContentReplacementState()
    msgs = [
        Message(
            role="assistant", content="", tool_calls=[{"id": f"t{i}"} for i in range(5)]
        ),
        Message(role="tool", content="A" * 45_000, tool_use_id="t0"),
        Message(role="tool", content="B" * 45_000, tool_use_id="t1"),
        Message(role="tool", content="C" * 45_000, tool_use_id="t2"),
        Message(role="tool", content="D" * 45_000, tool_use_id="t3"),
        Message(role="tool", content="E" * 45_000, tool_use_id="t4"),
    ]
    n = await offload_and_snip(msgs, state, sp)
    assert n == 1, f"应落 1 条达标，实际 {n}"
    replaced = [
        m.tool_use_id for m in msgs[1:] if m.content.startswith("[content offloaded]")
    ]
    assert len(replaced) == 1


@pytest.mark.anyio
async def test_single_threshold_takes_priority(tmp_path):
    """AC1：单条 > SINGLE_RESULT_THRESHOLD 必落（F1 优先于 F2 聚合）。"""
    _sc, sp = _setup(tmp_path)
    state = ContentReplacementState()
    # 1 条 60000（>50000 F1 必落）+ 1 条 100（小）
    msgs = [
        Message(role="assistant", content="", tool_calls=[{"id": "t0"}, {"id": "t1"}]),
        Message(role="tool", content="X" * 60_000, tool_use_id="t0"),
        Message(role="tool", content="Y" * 100, tool_use_id="t1"),
    ]
    n = await offload_and_snip(msgs, state, sp)
    assert n == 1, f"只有 t0 超 F1 阈值应落，实际 {n}"
    assert msgs[1].content.startswith("[content offloaded]")
    assert msgs[2].content == "Y" * 100


@pytest.mark.anyio
async def test_spill_idempotent(tmp_path):
    """AC3：同 id 两次落盘 → 文件 mtime 不变（幂等，不覆盖）。"""
    _sc, sp = _setup(tmp_path)
    state = ContentReplacementState()
    msgs = [Message(role="tool", content="X" * 60_000, tool_use_id="t1")]
    await offload_and_snip(msgs, state, sp)
    mtime1 = os.stat(tmp_path / "spill" / "t1").st_mtime_ns
    # 第二次（已 replaced，复用冻结预览，不再落盘）
    msgs2 = [Message(role="tool", content="X" * 60_000, tool_use_id="t1")]
    await offload_and_snip(msgs2, state, sp)
    mtime2 = os.stat(tmp_path / "spill" / "t1").st_mtime_ns
    assert mtime1 == mtime2, "幂等：不应重写文件"


@pytest.mark.anyio
async def test_decision_freeze(tmp_path):
    """AC4：同 id 两轮 → 预览逐字节一致（决策冻结，复用不重造）。"""
    _sc, sp = _setup(tmp_path)
    state = ContentReplacementState()
    msgs1 = [Message(role="tool", content="X" * 60_000, tool_use_id="t1")]
    await offload_and_snip(msgs1, state, sp)
    preview1 = msgs1[0].content
    msgs2 = [Message(role="tool", content="X" * 60_000, tool_use_id="t1")]
    await offload_and_snip(msgs2, state, sp)
    preview2 = msgs2[0].content
    assert preview1 == preview2, "冻结预览应逐字节一致"


@pytest.mark.anyio
async def test_spill_failure_retryable(tmp_path, monkeypatch):
    """AC4 子：落盘抛 OSError → 保持原文、不进账本、下轮重评。"""
    _sc, sp = _setup(tmp_path)
    state = ContentReplacementState()

    # monkeypatch _spill_to_path 抛 OSError
    import mewcode.context.offload as offload_mod

    original = offload_mod._spill_to_path

    def fail(path, content):
        raise OSError("disk full")

    monkeypatch.setattr(offload_mod, "_spill_to_path", fail)
    msgs = [Message(role="tool", content="X" * 60_000, tool_use_id="t1")]
    n = await offload_and_snip(msgs, state, sp)
    assert n == 0, "落盘失败不应计数替换"
    assert msgs[0].content == "X" * 60_000, "保持原文"
    # 账本未写：decision_for 仍 unseen
    assert state.decision_for("t1")[0] == "unseen"

    # 下轮恢复落盘能力 → 重评成功
    monkeypatch.setattr(offload_mod, "_spill_to_path", original)
    n2 = await offload_and_snip(msgs, state, sp)
    assert n2 == 1, "下轮应重评成功"
    assert msgs[0].content.startswith("[content offloaded]")


@pytest.mark.anyio
async def test_three_step_atomic(tmp_path, monkeypatch):
    """F2a：落盘失败时 content 未改写 + 账本未写（三步原子，无中间态）。"""
    _sc, sp = _setup(tmp_path)
    state = ContentReplacementState()

    import mewcode.context.offload as offload_mod

    def fail(path, content):
        raise OSError("fail")

    monkeypatch.setattr(offload_mod, "_spill_to_path", fail)
    original_content = "X" * 60_000
    msgs = [Message(role="tool", content=original_content, tool_use_id="t1")]
    await offload_and_snip(msgs, state, sp)
    # 三步原子：落盘失败 → content 未改写、账本未写
    assert msgs[0].content == original_content, "content 未改写"
    assert state.decision_for("t1")[0] == "unseen", "账本未写"


@pytest.mark.anyio
async def test_kept_decision_skipped(tmp_path):
    """防 bug：已 kept 的 id 不应被重新评估替换。

    手动标 kept 后 offload 应跳过、保持原文。
    """
    _sc, sp = _setup(tmp_path)
    state = ContentReplacementState()
    # 先标 kept
    state.decide_once("t1", "X" * 60_000, lambda: ("kept", ""))
    msgs = [Message(role="tool", content="X" * 60_000, tool_use_id="t1")]
    n = await offload_and_snip(msgs, state, sp)
    assert n == 0, "kept 不应被替换"
    assert msgs[0].content == "X" * 60_000


@pytest.mark.anyio
async def test_build_preview_stable():
    """防 bug：build_preview 非确定性 → 缓存失效。同输入必同输出。"""
    a = build_preview(60000, "head", "/tmp/f")
    b = build_preview(60000, "head", "/tmp/f")
    assert a == b
