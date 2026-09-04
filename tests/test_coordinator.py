"""Coordinator Mode 测试（ch15 F14/F52-F55）。

防的 bug：
- 双锁只开一把就生效（F14.1：feature AND env 两把都要）
- 收窄白名单漏 bash / 多出 write_file/edit_file（F14.3）
- 四阶段提示词缺「派完停手」纪律段（F55）
"""

from __future__ import annotations

from newcode.config.features import FeaturesConfig
from newcode.coordinator import (
    allowed_tools,
    env_truthy,
    is_enabled,
    system_prompt_suffix,
)


def test_double_lock(monkeypatch):
    cfg_on = FeaturesConfig(enable=True, coordinator_mode=True)
    cfg_off = FeaturesConfig(enable=True, coordinator_mode=False)
    monkeypatch.delenv("NEWCODE_COORDINATOR_MODE", raising=False)
    assert not is_enabled(cfg_on)  # flag 开 env 关 → False
    monkeypatch.setenv("NEWCODE_COORDINATOR_MODE", "1")
    assert is_enabled(cfg_on)  # 双开 → True
    assert not is_enabled(cfg_off)  # env 开 flag 关 → False
    monkeypatch.setenv("NEWCODE_COORDINATOR_MODE", "0")
    assert not is_enabled(cfg_on)


def test_env_truthy():
    assert (
        env_truthy("1")
        and env_truthy("true")
        and env_truthy("YES")
        and env_truthy(" yes ")
    )
    assert not env_truthy("0") and not env_truthy("off") and not env_truthy("")


def test_allowed_tools_narrowed():
    at = allowed_tools()
    for keep in (
        "Agent",
        "TeamCreate",
        "TeamDelete",
        "SendMessage",
        "bash",
        "read_file",
    ):
        assert keep in at
    assert "write_file" not in at and "edit_file" not in at  # F14.3 剥夺


def test_system_prompt_four_phases_and_discipline():
    s = system_prompt_suffix()
    for kw in ("Research", "Synthesis", "Implementation", "Verification", "派完就停手"):
        assert kw in s, kw
    # 纪律：禁止派完立刻自读探索 / 禁止轮询凑时间（F55）
    assert "禁止" in s and "轮询" in s
