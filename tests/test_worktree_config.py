"""ch14 worktree/config.py worktrees: 配置段测试。

防的 bug：
- worktrees: 键缺失被误读为全关闭（enable 应缺省 True）
- 三层合并优先级错（local 应覆盖 project/user）
- 非法数值静默吞掉而非降级缺省
"""

from __future__ import annotations

from pathlib import Path

from newcode.worktree.config import load_worktree_config


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_all_defaults_when_no_config(tmp_path):
    cfg = load_worktree_config(str(tmp_path))
    assert cfg.enable is True
    assert cfg.auto_cleanup is True
    assert cfg.background_cleanup is True
    assert cfg.cleanup_interval_minutes == 60.0
    assert cfg.expire_minutes == 180.0
    assert cfg.symlink_dirs == ["node_modules", ".venv", "vendor"]


def test_project_config_applies(tmp_path):
    _write(
        tmp_path / ".newcode" / "config.yaml",
        "worktrees:\n  enable: false\n  expire_minutes: 30\n",
    )
    cfg = load_worktree_config(str(tmp_path))
    assert cfg.enable is False
    assert cfg.expire_minutes == 30.0
    assert cfg.background_cleanup is True  # 未设字段保持缺省


def test_local_overrides_project(tmp_path):
    _write(
        tmp_path / ".newcode" / "config.yaml",
        "worktrees:\n  enable: true\n  expire_minutes: 30\n",
    )
    _write(
        tmp_path / ".newcode" / "config.local.yaml",
        "worktrees:\n  expire_minutes: 90\n",
    )
    cfg = load_worktree_config(str(tmp_path))
    assert cfg.enable is True  # 项目级字段保留
    assert cfg.expire_minutes == 90.0  # local 覆盖 project


def test_invalid_value_falls_back(tmp_path, capsys):
    _write(
        tmp_path / ".newcode" / "config.yaml",
        "worktrees:\n  enable: not-a-bool\n  expire_minutes: abc\n",
    )
    cfg = load_worktree_config(str(tmp_path))
    assert cfg.enable is True  # 非法 → 缺省
    assert cfg.expire_minutes == 180.0
    err = capsys.readouterr().err
    assert "非法" in err


def test_symlink_dirs_override(tmp_path):
    _write(
        tmp_path / ".newcode" / "config.yaml",
        "worktrees:\n  symlink_dirs: [node_modules]\n",
    )
    cfg = load_worktree_config(str(tmp_path))
    assert cfg.symlink_dirs == ["node_modules"]
