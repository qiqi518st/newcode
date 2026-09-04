"""ch14 worktree/create.py 创建后设置 A/B/C/D 测试（F4/AC5-AC8）。

防的 bug：
- 本地配置未复制进 worktree（子 Agent 在副本里拿不到 permissions/hooks 等运行时配置）
- 大依赖目录未软链（worktree 缺 node_modules 无法跑测试/构建）
- 被忽略但运行需要的文件（如 .env）未补全（.worktreeinclude 机制失效）
- setup 任一子步骤抛异常中断 create（N2：best-effort 只警告）
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from newcode.worktree.config import WorktreesConfig
from newcode.worktree.manager import Manager

pytestmark = pytest.mark.anyio


def _manager(repo: Path) -> Manager:
    return Manager(str(repo), WorktreesConfig())


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env={
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
        check=False,
    )
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


async def test_setup_a_copies_local_config(git_repo):
    """AC5：主仓库 .newcode/config.local.yaml → worktree 内同位置出现。"""
    cfg = git_repo / ".newcode" / "config.local.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("worktrees:\n  enable: false\n", encoding="utf-8")
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    copied = (
        git_repo / ".newcode" / "worktrees" / "alice" / ".newcode" / "config.local.yaml"
    )
    assert copied.exists()
    assert "enable: false" in copied.read_text(encoding="utf-8")


async def test_setup_b_hooks_husky(git_repo):
    """AC6：主仓库 .husky/ → worktree .git/config 含 core.hooksPath。"""
    husky = git_repo / ".husky"
    husky.mkdir()
    (husky / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    wt = git_repo / ".newcode" / "worktrees" / "alice"
    got = _git(wt, "config", "--get", "core.hooksPath")
    assert got == str(husky.resolve())


async def test_setup_c_symlink_node_modules(git_repo):
    """AC7：主仓库 node_modules/ → worktree 内是 symlink。"""
    (git_repo / "node_modules").mkdir()
    (git_repo / "node_modules" / "pkg").write_text("x\n", encoding="utf-8")
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    link = git_repo / ".newcode" / "worktrees" / "alice" / "node_modules"
    assert link.is_symlink()
    assert (link / "pkg").read_text(encoding="utf-8") == "x\n"


async def test_setup_d_worktreeinclude(git_repo):
    """AC8：.worktreeinclude 含 *.env 且主仓库有被忽略 .env → worktree 内出现 .env。"""
    (git_repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    (git_repo / ".worktreeinclude").write_text("*.env\n", encoding="utf-8")
    (git_repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    env = git_repo / ".newcode" / "worktrees" / "alice" / ".env"
    assert env.exists()
    assert env.read_text(encoding="utf-8") == "SECRET=1\n"


async def test_setup_failures_do_not_block_create(git_repo):
    """N2：任一 setup 子步骤失败仅警告，不中断 create。"""
    # 无 .husky / 无 node_modules / 无 .worktreeinclude / 无 .newcode → 全部跳过
    m = _manager(git_repo)
    wt = await m.create("alice", "HEAD", manual=True)
    assert wt.branch == "worktree-alice"
    assert (git_repo / ".newcode" / "worktrees" / "alice").is_dir()
