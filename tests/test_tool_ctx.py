"""ch14 tools/cwd.py ctx cwd 通道测试（AC22）。

防的 bug：
- with_cwd 未恢复 token（嵌套 / 异常后泄漏到外层，污染后续工具调用）
- resolve_path 相对路径拼到进程 cwd 而非 ctx cwd（worktree 隔离失效）
"""

from __future__ import annotations

from pathlib import Path

from mewcode.tools.cwd import cwd_from_ctx, resolve_path, with_cwd


def test_resolve_absolute_passthrough(tmp_path):
    p = str(tmp_path / "f.txt")
    with with_cwd(str(tmp_path)):
        assert resolve_path(p) == p


def test_resolve_relative_uses_ctx_cwd(tmp_path):
    with with_cwd(str(tmp_path)):
        assert resolve_path("a/b.txt") == str(tmp_path / "a/b.txt")


def test_resolve_relative_falls_back_to_process_cwd():
    with with_cwd(""):
        assert resolve_path("x.txt") == str(Path.cwd() / "x.txt")


def test_resolve_empty_returns_base():
    with with_cwd("/tmp"):
        assert resolve_path("") == "/tmp"


def test_with_cwd_restores_after_exit(tmp_path):
    assert cwd_from_ctx() is None
    with with_cwd(str(tmp_path)):
        assert cwd_from_ctx() == str(tmp_path)
    assert cwd_from_ctx() is None  # token 已恢复


def test_with_cwd_nested(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    with with_cwd(str(a)):
        with with_cwd(str(b)):
            assert cwd_from_ctx() == str(b)
        assert cwd_from_ctx() == str(a)


def test_with_cwd_empty_is_noop():
    with with_cwd(""):
        assert cwd_from_ctx() is None
