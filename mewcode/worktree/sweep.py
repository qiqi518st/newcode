"""sweep_stale 后台过期清理（ch14 F6.4/F6.5）：三层过滤，fail-closed。

1. 命名分类——仅 agent-a[0-9a-f]{7} 或 wf- 前缀的自动创建 worktree 才可能被清
2. 时间 + 使用中——目录 mtime > cutoff 跳过；当前 session 路径跳过
3. 变更保护——_has_worktree_changes True 跳过（fail-closed）；未推送 commit 跳过
通过三层者调 remove(discard_changes=True)。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .git import _has_worktree_changes, rev_list_unpushed
from .types import ExitOptions

if TYPE_CHECKING:
    from .manager import Manager

EPHEMERAL_PATTERN = re.compile(r"^agent-a[0-9a-f]{7}$")


async def sweep_stale(self: Manager, cutoff: datetime) -> list[str]:
    """清理过期的临时 worktree，返回被移除的名字列表（F6.4）。"""
    removed: list[str] = []
    if not self.worktree_dir.is_dir():
        return removed
    for child in self.worktree_dir.iterdir():
        if not child.is_dir():
            continue
        name = child.name

        # 第一层：命名分类（仅自动创建的临时 worktree，F1.3/F6.4）
        if not (EPHEMERAL_PATTERN.match(name) or name.startswith("wf-")):
            continue

        # 第二层：过期 + 非使用中（当前 session）
        try:
            mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime > cutoff:
            continue
        if self._current_session and self._current_session.worktree_path == str(child):
            continue

        # 第三层：fail-closed 变更检查（_has_worktree_changes 含 base..HEAD 新增 commit，
        # 无远端仓库也能正确判断——clean+无新增 commit 可清）；孤儿（base 未知）时用
        # --not --remotes 兜底未推送 commit（F6.4）
        base = ""
        wt = self.active.get(name)
        if wt is not None:
            base = wt.head_commit
        try:
            changed = await _has_worktree_changes(str(child), base)
            unpushed = (not base) and bool(await rev_list_unpushed(str(child)))
            if changed or unpushed:
                continue
        except Exception:  # noqa: S112, BLE001 - fail-closed 宁可保留
            continue

        try:
            await self.remove(name, ExitOptions(discard_changes=True))
            removed.append(name)
        except Exception:  # noqa: S112, BLE001 - 单目录失败不影响其他
            continue
    return removed
