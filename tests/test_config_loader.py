"""load_ccswitch 共享配置检测测试。

背景（防回归）：load_ccswitch 曾硬编码 Path.home()/.claude/settings.json，
在 CLAUDE_CONFIG_DIR 指向其它目录（如 WSL 里指向 /mnt/c/Users/... 的 Windows
侧配置）时读不到最新的 ANTHROPIC_API_KEY，回退到 .mewcode.yaml 里过期的 key。
修复后优先读 CLAUDE_CONFIG_DIR/settings.json，且同时认 API_KEY 与 AUTH_TOKEN。
"""

import json
import pathlib

from mewcode.config.loader import load_ccswitch


def _write_settings(tmp_path, env: dict) -> pathlib.Path:
    """在 tmp_path/claude/ 下写一个 settings.json 并返回目录"""
    d = tmp_path / "claude"
    d.mkdir()
    d.joinpath("settings.json").write_text(json.dumps({"env": env}), encoding="utf-8")
    return d


class TestLoadCcswitch:
    def test_reads_api_key_from_claude_config_dir(self, tmp_path, monkeypatch):
        """CLAUDE_CONFIG_DIR 指向的 settings.json 里的 ANTHROPIC_API_KEY 被读取"""
        claude_dir = _write_settings(
            tmp_path,
            {
                "ANTHROPIC_API_KEY": "sk-newkey",
                "ANTHROPIC_BASE_URL": "https://opencode.ai/zen/go",
                "ANTHROPIC_MODEL": "deepseek-v4-flash[1M]",
            },
        )
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))

        cfg = load_ccswitch()

        assert cfg is not None
        assert cfg.provider == "ccswitch"
        p = cfg.providers[0]
        assert p.protocol == "anthropic"
        assert p.api_key == "sk-newkey"
        assert p.auth_token is None
        assert p.base_url == "https://opencode.ai/zen/go"
        assert p.model == "deepseek-v4-flash[1M]"

    def test_reads_auth_token_from_claude_config_dir(self, tmp_path, monkeypatch):
        """AUTH_TOKEN（Bearer）也认，保持旧 CC Switch 行为兼容"""
        claude_dir = _write_settings(tmp_path, {"ANTHROPIC_AUTH_TOKEN": "bearer-tok"})
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))

        cfg = load_ccswitch()

        assert cfg is not None
        p = cfg.providers[0]
        assert p.api_key == ""
        assert p.auth_token == "bearer-tok"

    def test_falls_back_to_home_claude(self, tmp_path, monkeypatch):
        """未设置 CLAUDE_CONFIG_DIR 时回退 ~/.claude/settings.json"""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(
            json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-homekey"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

        cfg = load_ccswitch()

        assert cfg is not None
        assert cfg.providers[0].api_key == "sk-homekey"

    def test_missing_settings_file_returns_none(self, tmp_path, monkeypatch):
        """CLAUDE_CONFIG_DIR 下没有 settings.json → None，落到 .mewcode.yaml"""
        d = tmp_path / "empty"
        d.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(d))

        assert load_ccswitch() is None

    def test_missing_key_returns_none(self, tmp_path, monkeypatch):
        """env 里既无 API_KEY 也无 AUTH_TOKEN → None"""
        claude_dir = _write_settings(tmp_path, {"ANTHROPIC_MODEL": "m"})
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))

        assert load_ccswitch() is None

    def test_default_model(self, tmp_path, monkeypatch):
        """env 缺 ANTHROPIC_MODEL 时用默认 anthropic 模型名"""
        claude_dir = _write_settings(tmp_path, {"ANTHROPIC_API_KEY": "sk-x"})
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))

        cfg = load_ccswitch()

        assert cfg is not None
        assert cfg.providers[0].model == "claude-sonnet-4-20250514"
