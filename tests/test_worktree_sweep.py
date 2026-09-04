"""ch14 worktree/sweep.py sweep_stale 三层清理测试（F6.4/AC19）。

防的 bug：
- sweep 误删手动创建或非 agent-a/wf- 命名的 worktree（第一层分类失效）
- sweep 删未过期或当前 session 的 worktree（第二层）
- sweep 删有未提交修改 / 未推送 commit 的 worktree（第三层，防丢代码）
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
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


def _age_dir(path: Path, days: int) -> None:
    """把目录 mtime 调老（模拟过期）。"""
    old = datetime.now(timezone.utc) - timedelta(days=days)
    ts = old.timestamp()
    os.utime(path, (ts, ts))


async def _make_agent_worktree(m: Manager, name: str) -> Path:
    wt = await m.create(name, "HEAD", manual=False)
    return Path(wt.path)


async def test_sweep_removes_clean_expired_auto(git_repo):
    m = _manager(git_repo)
    wt = await _make_agent_worktree(m, "agent-a1b2c3de")
    _age_dir(wt, 5)
    removed = await m.sweep_stale(datetime.now(timezone.utc) - timedelta(hours=24))
    assert removed == ["agent-a1b2c3de"]
    assert not wt.exists()
    assert m.get("agent-a1b2c3de") is None


async def test_sweep_skips_non_auto_name(git_repo):
    """第一层：手动命名（非 agent-a/wf-）不清。"""
    m = _manager(git_repo)
    wt = await _make_agent_worktree(m, "review-fix")
    _age_dir(wt, 5)
    removed = await m.sweep_stale(datetime.now(timezone.utc) - timedelta(hours=24))
    assert removed == []
    assert wt.exists()


async def test_sweep_skips_not_expired(git_repo):
    """第二层：未过期不清。"""
    m = _manager(git_repo)
    wt = await _make_agent_worktree(m, "agent-a1b2c3de")  # mtime 现在 → 未过期
    removed = await m.sweep_stale(datetime.now(timezone.utc) - timedelta(hours=24))
    assert removed == []
    assert wt.exists()


async def test_sweep_skips_current_session(git_repo):
    """第二层：当前 session 路径跳过。"""
    m = _manager(git_repo)
    wt = await _make_agent_worktree(m, "agent-a1b2c3de")
    _age_dir(wt, 5)
    await m.enter("agent-a1b2c3de")
    removed = await m.sweep_stale(datetime.now(timezone.utc) - timedelta(hours=24))
    assert removed == []
    assert wt.exists()


async def test_sweep_skips_dirty(git_repo):
    """第三层：过期但有未提交修改 → 保留。"""
    m = _manager(git_repo)
    wt = await _make_agent_worktree(m, "agent-a1b2c3de")
    (wt / "a.txt").write_text("dirty\n", encoding="utf-8")
    _age_dir(wt, 5)
    removed = await m.sweep_stale(datetime.now(timezone.utc) - timedelta(hours=24))
    assert removed == []
    assert wt.exists()


async def test_sweep_skips_unpushed_commit(git_repo):
    """第三层：过期但新增 commit（无远端视为未推送）→ 保留。"""
    m = _manager(git_repo)
    wt = await _make_agent_worktree(m, "agent-a1b2c3de")
    _git(wt, "commit", "-q", "--allow-empty", "-m", "wip")
    _age_dir(wt, 5)
    removed = await m.sweep_stale(datetime.now(timezone.utc) - timedelta(hours=24))
    assert removed == []
    assert wt.exists()


async def test_sweep_keeps_wf_prefix_auto(git_repo):
    """wf- 前缀视为自动创建，过期+干净可清（已裁决兼容 wf-）。"""
    m = _manager(git_repo)
    wt = await _make_agent_worktree(m, "wf-task1")
    _age_dir(wt, 5)
    removed = await m.sweep_stale(datetime.now(timezone.utc) - timedelta(hours=24))
    assert removed == ["wf-task1"]
    assert not wt.exists()
