"""ch14 worktree/manager.py Manager 构造 / create / session / 降级测试。

防的 bug：
- create 未建 worktree 目录/分支（AC2/AC3）
- 目录已存在时重复 create 重跑 git worktree add（AC4 快速恢复必须零 git）
- session 指向的 worktree 被外部删除后启动误恢复（AC20 应清空+警告）
- 非 git 目录构造 Manager 不抛异常（AC24 降级）
- .gitignore 缺失时自动修改（AC21 只警告不改）
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from newcode.worktree.config import WorktreesConfig
from newcode.worktree.manager import Manager
from newcode.worktree.types import (
    WorktreeError,
    WorktreeExistsError,
    WorktreeNotFoundError,
)

pytestmark = pytest.mark.anyio


def _manager(repo: Path) -> Manager:
    return Manager(str(repo), WorktreesConfig())


async def test_create_flat_slug_dir_and_branch(git_repo):
    m = _manager(git_repo)
    wt = await m.create("alice", "HEAD", manual=True)
    assert wt.path == str(git_repo / ".newcode" / "worktrees" / "alice")
    assert wt.branch == "worktree-alice"
    assert (git_repo / ".newcode" / "worktrees" / "alice").is_dir()
    assert wt in m.list()
    assert m.get("alice") is wt


async def test_create_nested_slug_flattened(git_repo):
    m = _manager(git_repo)
    wt = await m.create("team-refactor/alice", "HEAD", manual=True)
    assert wt.branch == "worktree-team-refactor+alice"
    assert (git_repo / ".newcode" / "worktrees" / "team-refactor+alice").is_dir()


async def test_create_duplicate_raises(git_repo):
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    with pytest.raises(WorktreeExistsError):
        await m.create("alice", "HEAD", manual=True)


async def test_manual_create_rejects_auto_prefix(git_repo):
    m = _manager(git_repo)
    with pytest.raises(ValueError, match="agent"):
        await m.create("agent-a1b2c3de", "HEAD", manual=True)


async def test_create_fast_recovery_no_git(git_repo, monkeypatch):
    """AC4：目录已存在（外部残留）再 create → 快速恢复，不调 git worktree add。"""
    from newcode.worktree import git as g

    m = _manager(git_repo)
    wt_dir = git_repo / ".newcode" / "worktrees" / "alice"
    wt_dir.parent.mkdir(parents=True, exist_ok=True)
    # 模拟崩溃残留：外部直接 git worktree add（绕过 Manager.create）
    await asyncio.to_thread(
        subprocess.run,
        [
            "git",
            "-C",
            str(git_repo),
            "worktree",
            "add",
            "-B",
            "worktree-alice",
            str(wt_dir),
            "HEAD",
        ],
        check=True,
        capture_output=True,
    )
    calls: list = []
    orig = g.worktree_add

    async def fake_worktree_add(*a, **k):
        calls.append(a)
        return await orig(*a, **k)

    monkeypatch.setattr(g, "worktree_add", fake_worktree_add)
    wt = await m.create("alice", "HEAD", manual=True)
    assert wt.path == str(wt_dir)
    assert calls == []  # 快速恢复零 git 子进程


async def test_scan_restores_active(git_repo):
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    m2 = _manager(git_repo)  # 模拟重启：扫描还原 active
    assert m2.get("alice") is not None
    assert m2.get("alice").head_commit == m.get("alice").head_commit


async def test_enter_does_not_chdir(git_repo):
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    cwd_before = Path.cwd()
    session = await m.enter("alice")
    assert Path.cwd() == cwd_before  # 不 os.chdir（AC9）
    assert session.worktree_name == "alice"
    assert session.original_cwd == str(cwd_before)
    assert m.current_session() is session


async def test_enter_not_found(git_repo):
    m = _manager(git_repo)
    with pytest.raises(WorktreeNotFoundError):
        await m.enter("nope")


async def test_session_persisted_and_resumed(git_repo):
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    session = await m.enter("alice")
    m2 = _manager(git_repo)  # 模拟重启：session 从文件恢复（AC20）
    assert m2.current_session() is not None
    assert m2.current_session().worktree_name == "alice"
    assert m2.current_session().session_id == session.session_id


async def test_session_cleared_when_worktree_gone(git_repo, capsys):
    """AC20：session 指向的 worktree 被外部删除 → 启动清空 + stderr 警告。"""
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    await m.enter("alice")
    # 外部删除 worktree 目录
    import shutil

    shutil.rmtree(git_repo / ".newcode" / "worktrees" / "alice", ignore_errors=True)
    m2 = _manager(git_repo)
    assert m2.current_session() is None
    assert "已清空" in capsys.readouterr().err


async def test_gitignore_missing_only_warns(git_repo, capsys):
    """AC21：根 .gitignore 缺两行 → stderr 警告，不修改文件。"""
    gi = git_repo / ".gitignore"
    _manager(git_repo)  # 触发 check_gitignore（缺两行 → 警告）
    err = capsys.readouterr().err
    assert "只警告不修改" in err or "未包含" in err
    if gi.exists():
        assert ".newcode/worktrees/" not in gi.read_text(encoding="utf-8")
    else:
        assert not gi.exists()  # 未自动创建


async def test_gitignore_present_no_warn(git_repo, capsys):
    gi = git_repo / ".gitignore"
    gi.write_text(
        ".newcode/worktrees/\n.newcode/worktree_session.json\n", encoding="utf-8"
    )
    _manager(git_repo)
    err = capsys.readouterr().err
    assert "未包含" not in err


def test_not_a_git_repo_raises(tmp_path):
    """AC24：非 git 目录构造 Manager 抛 WorktreeError（main.py 据此降级）。"""
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(WorktreeError):
        Manager(str(plain), WorktreesConfig())


async def test_run_sweeps_then_sleeps(git_repo, monkeypatch):
    """F6.5：run() 首轮立即 sweep，然后按 interval 循环（interval 置 0 快速退出）。"""
    m = _manager(git_repo)
    m.cfg.background_cleanup = True
    m.cfg.cleanup_interval_minutes = 0.0  # 0 → sleep(0) 立即重试，两轮后停止
    calls = []
    orig = m.sweep_stale

    async def fake_sweep(cutoff):
        calls.append(cutoff)
        await orig(cutoff)
        if len(calls) >= 2:
            m.cfg.background_cleanup = False
        return []

    m.sweep_stale = fake_sweep  # type: ignore[method-assign]
    import asyncio

    await asyncio.wait_for(m.run(), timeout=10)
    assert len(calls) >= 1
