"""enter / exit / remove / auto_cleanup（ch14 F5/F6.1-F6.3）。

- enter：锁内取 active → 记原状态 → 构造 session → 持久化；**不 os.chdir**（G6）
- exit：锁内校验当前 → REMOVE 且未 discard 查变更 → os.chdir 兜底（N4）→ 持久化 null →
  REMOVE: worktree remove --force + sleep(0.1) + branch -D（先查后强删，N8）
- remove：独立入口，可删非当前 session；变更保护同 exit
- auto_cleanup：manual → keep；无变更 → remove；有变更 → keep（F6.1/F6.2）
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from .git import (
    _has_worktree_changes,
    branch_delete,
    current_branch,
    current_head,
    worktree_remove_force,
)
from .session import WorktreeSession, save_session
from .types import (
    AutoCleanupReport,
    ExitAction,
    ExitOptions,
    ExitReport,
    WorktreeError,
    WorktreeHasChangesError,
    WorktreeNotFoundError,
)

if TYPE_CHECKING:
    from .manager import Manager


async def enter(self: Manager, name: str) -> WorktreeSession:
    """进入 worktree：记录会话并持久化，不 chdir（F5.1/G6）。"""
    async with self._lock:
        wt = self.active.get(name)
        if wt is None:
            raise WorktreeNotFoundError(f"worktree 不存在: {name}")

    original_cwd = str(Path.cwd())
    try:
        original_branch = await current_branch(self.repo_root)
    except Exception:  # noqa: BLE001 - 原状态尽力而为
        original_branch = ""
    try:
        original_head = await current_head(self.repo_root)
    except Exception:  # noqa: BLE001
        original_head = ""

    session = WorktreeSession(
        original_cwd=original_cwd,
        worktree_path=wt.path,
        worktree_name=name,
        original_branch=original_branch,
        original_head_commit=original_head,
        session_id=secrets.token_hex(8),
    )
    async with self._lock:
        self._current_session = session
    save_session(self.session_file, session)
    return session


async def exit(
    self: Manager, name: str, action: ExitAction, opts: ExitOptions
) -> ExitReport:
    """退出当前 session（F5.2）；只能退当前，REMOVE 带变更保护。"""
    async with self._lock:
        wt = self.active.get(name)
        if wt is None:
            raise WorktreeNotFoundError(f"worktree 不存在: {name}")
        session = self._current_session
        if session is None or session.worktree_name != name:
            raise WorktreeError("只能退出当前活跃的 worktree")

    if (
        action == ExitAction.REMOVE
        and not opts.discard_changes
        and await _has_worktree_changes(wt.path, wt.head_commit)
    ):
        raise WorktreeHasChangesError(
            f"worktree {name} 有未提交修改或新增 commit，拒绝删除"
        )

    # N4：os.chdir 仅在此兜底（防 session 期间进程 cwd 残留）
    with contextlib.suppress(OSError):
        os.chdir(session.original_cwd)

    async with self._lock:
        self._current_session = None
    save_session(self.session_file, None)

    if action == ExitAction.REMOVE:
        await worktree_remove_force(self.repo_root, wt.path)
        await asyncio.sleep(0.1)  # git lockfile 竞态，100ms 经验值
        await branch_delete(self.repo_root, wt.branch)
        async with self._lock:
            self.active.pop(name, None)

    return ExitReport(
        removed=action == ExitAction.REMOVE, path=wt.path, branch=wt.branch
    )


async def remove(self: Manager, name: str, opts: ExitOptions) -> ExitReport:
    """独立 remove 入口（F5.3）：可删非当前 session；变更保护同 exit。"""
    async with self._lock:
        wt = self.active.get(name)
        if wt is None:
            raise WorktreeNotFoundError(f"worktree 不存在: {name}")

    if not opts.discard_changes and await _has_worktree_changes(
        wt.path, wt.head_commit
    ):
        raise WorktreeHasChangesError(
            f"worktree {name} 有未提交修改或新增 commit，拒绝删除"
        )

    async with self._lock:
        if self._current_session and self._current_session.worktree_name == name:
            self._current_session = None
            save_session(self.session_file, None)

    await worktree_remove_force(self.repo_root, wt.path)
    await asyncio.sleep(0.1)
    await branch_delete(self.repo_root, wt.branch)
    async with self._lock:
        self.active.pop(name, None)
    return ExitReport(removed=True, path=wt.path, branch=wt.branch)


async def auto_cleanup(self: Manager, name: str) -> AutoCleanupReport:
    """子 Agent 完成后的自动清理（F6.1/F6.2）：manual 跳过、无变更清除、有变更保留。"""
    async with self._lock:
        wt = self.active.get(name)
    if wt is None:
        return AutoCleanupReport(kept=False)  # 已不存在，无需处理
    if wt.manual:
        return AutoCleanupReport(kept=True, path=wt.path, branch=wt.branch)
    if await _has_worktree_changes(wt.path, wt.head_commit):
        return AutoCleanupReport(kept=True, path=wt.path, branch=wt.branch)
    await self.remove(name, ExitOptions(discard_changes=True))
    return AutoCleanupReport(kept=False)
