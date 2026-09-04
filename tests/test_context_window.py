"""Context Window 四级解析单测（ch08 T27，spec F29）。

防 bug：解析顺序错乱或某级异常未兜底导致抛异常/取错值。
"""

import pytest

from newcode.context import capabilities as cap_mod
from newcode.context.window import get_context_window_for_model


def test_env_override(monkeypatch):
    """第 1 级：env CLAUDE_CODE_MAX_CONTEXT_TOKENS 命中即取该值。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "123456")
    assert get_context_window_for_model("any-model", "anthropic") == 123456


def test_env_invalid_falls_through(monkeypatch):
    """防 bug：env 非数字时应跳下级，而非抛 ValueError 崩溃。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "not-a-number")
    # 应落入后续级别：[1m] 或能力表或协议默认
    assert get_context_window_for_model("gpt-4o", "openai") == 128_000


def test_one_m_suffix(monkeypatch):
    """第 2 级：模型名带 [1m] 后缀 → 1,000,000。"""
    monkeypatch.delenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", raising=False)
    assert get_context_window_for_model("claude-sonnet-4[1m]", "anthropic") == 1_000_000


def test_suffix_case_insensitive_and_various(monkeypatch):
    """第 2 级：后缀单位大小写不敏感，且不写死 1M（支持 512K/2M 等）。

    防回归：曾写死只认小写 [1m] 且只认 1M，CC Switch 写入的大写 [1M]
    识别不到，悄悄回落协议默认。
    """
    monkeypatch.delenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", raising=False)
    assert (
        get_context_window_for_model("deepseek-v4-flash[1M]", "anthropic") == 1_000_000
    )
    assert get_context_window_for_model("model[512k]", "anthropic") == 512_000
    assert get_context_window_for_model("model[2M]", "anthropic") == 2_000_000
    assert get_context_window_for_model("model[10K]", "openai") == 10_000


def test_capability_table(monkeypatch):
    """第 3 级：能力表命中且 ≥100K → 取表值。"""
    monkeypatch.delenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", raising=False)
    assert get_context_window_for_model("gpt-4o", "openai") == 128_000


def test_capability_below_floor_falls_through(monkeypatch):
    """防 bug：<100K 的表值不应被第 3 级采用，应落第 4 级协议默认。

    临时塞一个 50K 表值验证 floor 守卫。
    """
    monkeypatch.delenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", raising=False)
    monkeypatch.setitem(cap_mod.CAPABILITIES, "tiny-model", 50_000)
    try:
        # 50K < CAPABILITY_TABLE_FLOOR(100K) → 落协议默认
        assert get_context_window_for_model("tiny-model", "openai") == 128_000
    finally:
        cap_mod.CAPABILITIES.pop("tiny-model", None)


def test_protocol_default_anthropic(monkeypatch):
    """第 4 级：未知模型 + anthropic → 200000。"""
    monkeypatch.delenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", raising=False)
    assert get_context_window_for_model("unknown-model", "anthropic") == 200_000


def test_protocol_default_openai(monkeypatch):
    """第 4 级：未知模型 + openai → 128000。"""
    monkeypatch.delenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", raising=False)
    assert get_context_window_for_model("unknown-model", "openai") == 128_000


def test_protocol_default_unknown(monkeypatch):
    """第 4 级：未知协议 → 保守 anthropic 默认 200000。"""
    monkeypatch.delenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", raising=False)
    assert get_context_window_for_model("unknown-model", "unknown-protocol") == 200_000


def test_env_priority_over_suffix(monkeypatch):
    """防 bug：env 应优先于 [1m] 后缀（第 1 级 > 第 2 级）。"""
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "999")
    assert get_context_window_for_model("model[1m]", "anthropic") == 999


@pytest.mark.anyio
async def test_never_raises():
    """防 bug：任意异常都应落入第 4 级，永不抛。"""
    # 各种极端输入都不应抛异常
    for model in ["", "None", "模型[1m]", "a" * 1000]:
        get_context_window_for_model(model, "anthropic")  # 不抛即过
