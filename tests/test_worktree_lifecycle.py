"""ch14 worktree/lifecycle.py enter/exit/remove/auto_cleanup 测试（F5/F6.1-F6.3）。

防的 bug：
- exit(REMOVE) 在 worktree 有未提交修改时删除（AC10 必须抛 WorktreeHasChangesError 并保留目录）
- exit 未切回 original_cwd（N4 兜底）
- auto_cleanup 误删 manual 创建或有变更的 worktree（AC12）
- remove 删的是当前 session 却不清 session 文件（残留导致重启误恢复）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mewcode.worktree.config import WorktreesConfig
from mewcode.worktree.manager import Manager
from mewcode.worktree.types import (
    ExitAction,
    ExitOptions,
    WorktreeError,
    WorktreeHasChangesError,
)

pytestmark = pytest.mark.anyio


def _manager(repo: Path) -> Manager:
    return Manager(str(repo), WorktreesConfig())


def _wt(m: Manager, name: str) -> Path:
    return Path(m.get(name).path)


async def test_exit_keep_clears_session(git_repo):
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    await m.enter("alice")
    report = await m.exit("alice", ExitAction.KEEP, ExitOptions())
    assert report.removed is False
    assert m.current_session() is None
    assert _wt(m, "alice").exists()  # KEEP 不删目录


async def test_exit_remove_refuses_dirty(git_repo):
    """AC10：exit(REMOVE) 遇未提交修改 → 抛 WorktreeHasChangesError，目录仍在。"""
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    await m.enter("alice")
    (_wt(m, "alice") / "a.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(WorktreeHasChangesError):
        await m.exit("alice", ExitAction.REMOVE, ExitOptions())
    assert _wt(m, "alice").exists()  # 目录未被删
    # 当前 session 仍在（exit 未完成）
    assert m.current_session() is not None


async def test_exit_remove_discard_deletes(git_repo):
    """AC11：exit(REMOVE, discard_changes=True) → 目录删、分支删。"""
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    wt_path = _wt(m, "alice")
    await m.enter("alice")
    (wt_path / "a.txt").write_text("dirty\n", encoding="utf-8")
    report = await m.exit("alice", ExitAction.REMOVE, ExitOptions(discard_changes=True))
    assert report.removed is True
    assert not wt_path.exists()
    assert m.get("alice") is None
    # 分支已删；to_thread 避免阻塞事件循环（ASYNC221）
    import asyncio
    import subprocess

    proc = await asyncio.to_thread(
        subprocess.run,
        ["git", "branch"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "worktree-alice" not in proc.stdout


async def test_exit_remove_clean_deletes(git_repo):
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    await m.enter("alice")
    report = await m.exit("alice", ExitAction.REMOVE, ExitOptions())
    assert report.removed is True
    assert m.get("alice") is None


async def test_exit_only_current_session(git_repo):
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    await m.create("bob", "HEAD", manual=True)
    await m.enter("alice")
    with pytest.raises(WorktreeError):
        await m.exit("bob", ExitAction.KEEP, ExitOptions())


async def test_remove_other_not_current(git_repo):
    """F5.3：remove 可删非当前 session 的 worktree。"""
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    await m.create("bob", "HEAD", manual=True)
    await m.enter("alice")
    report = await m.remove("bob", ExitOptions())
    assert report.removed is True
    assert m.get("bob") is None
    assert m.current_session() is not None  # alice session 不受影响


async def test_auto_cleanup_manual_kept(git_repo):
    """AC12：manual=True 直接 keep。"""
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    report = await m.auto_cleanup("alice")
    assert report.kept is True
    assert _wt(m, "alice").exists()


async def test_auto_cleanup_no_changes_removes(git_repo):
    m = _manager(git_repo)
    wt = await m.create("agent-a1b2c3de", "HEAD", manual=False)
    report = await m.auto_cleanup("agent-a1b2c3de")
    assert report.kept is False
    assert not Path(wt.path).exists()
    assert m.get("agent-a1b2c3de") is None


async def test_auto_cleanup_has_changes_kept(git_repo):
    m = _manager(git_repo)
    wt = await m.create("agent-a1b2c3de", "HEAD", manual=False)
    Path(wt.path, "a.txt").write_text("changed\n", encoding="utf-8")
    report = await m.auto_cleanup("agent-a1b2c3de")
    assert report.kept is True
    assert report.path == wt.path
    assert report.branch == "worktree-agent-a1b2c3de"
    assert Path(wt.path).exists()


async def test_remove_clears_session_file(git_repo):
    """remove 当前 session 的 worktree → session 文件清空（防重启误恢复）。"""
    m = _manager(git_repo)
    await m.create("alice", "HEAD", manual=True)
    await m.enter("alice")
    await m.remove("alice", ExitOptions(discard_changes=True))
    assert m.current_session() is None
    raw = m.session_file.read_text(encoding="utf-8").strip()
    assert raw == "null"
