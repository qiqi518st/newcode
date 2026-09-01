"""ch13 subagent/config.py agents: 配置段测试。

防的 bug：
- agents: 键缺失被误读为全启用（enable_verifier 应为 False）
- 三层合并优先级错（local 应覆盖 project/user）
- 非法数值静默吞掉而非降级缺省（async_timeout_s 等应回退默认值）
"""

from __future__ import annotations

from pathlib import Path

from mewcode.subagent.config import load_agent_config


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_all_defaults_when_no_config(tmp_path):
    cfg = load_agent_config(str(tmp_path))
    assert cfg.enable_verifier is False
    assert cfg.enable_subagent_background is True
    assert cfg.async_timeout_s == 120.0
    assert cfg.idle_cleanup_minutes == 15.0
    assert cfg.max_idle_agents == 10
    assert cfg.max_tasks_per_agent == 10
    assert cfg.max_queue_per_agent == 2
    assert cfg.model_tiers == {}
    assert cfg.effective_enable_subagent_background() is True


def test_project_config_applies(tmp_path):
    _write(tmp_path / ".mewcode" / "config.yaml", "agents:\n  enable_verifier: true\n")
    cfg = load_agent_config(str(tmp_path))
    assert cfg.enable_verifier is True


def test_local_overrides_project(tmp_path):
    _write(
        tmp_path / ".mewcode" / "config.yaml",
        "agents:\n  enable_verifier: true\n  async_timeout_s: 10\n",
    )
    _write(
        tmp_path / ".mewcode" / "config.local.yaml",
        "agents:\n  async_timeout_s: 30\n",
    )
    cfg = load_agent_config(str(tmp_path))
    assert cfg.enable_verifier is True  # 项目级字段保留
    assert cfg.async_timeout_s == 30.0  # local 覆盖


def test_model_tiers(tmp_path):
    _write(
        tmp_path / ".mewcode" / "config.yaml",
        "agents:\n  model_tiers: {haiku: h-1, sonnet: s-1}\n",
    )
    cfg = load_agent_config(str(tmp_path))
    assert cfg.model_tiers == {"haiku": "h-1", "sonnet": "s-1"}


def test_invalid_numeric_falls_back(capsys, tmp_path):
    _write(tmp_path / ".mewcode" / "config.yaml", "agents:\n  max_idle_agents: abc\n")
    cfg = load_agent_config(str(tmp_path))
    assert cfg.max_idle_agents == 10
    assert "非法" in capsys.readouterr().err
