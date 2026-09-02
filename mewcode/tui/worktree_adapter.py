"""WorktreeAccessor 的 TUI 适配器（ch14 F9）：包装 worktree.Manager + 同步 active_cwd。

位于 tui 层：slash 命令经 ctx.ui.worktree_accessor() 拿到此适配器（协议隔离，
slash 不依赖 worktree 包）。enter 成功时把 worktree 路径写回 REPL.active_cwd，
主 Agent 后续 Run 经 with_cwd 注入该 cwd（F9.3）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..slash.ui import WorktreeSummary

if TYPE_CHECKING:
    from ..worktree.manager import Manager


class WorktreeAdapter:
    """实现 slash.ui.WorktreeAccessor（结构上鸭子类型兼容协议）。"""

    def __init__(self, manager: Manager, set_active_cwd: Callable[[str], None]) -> None:
        self._manager = manager
        self._set_active_cwd = set_active_cwd

    async def create(self, name: str) -> tuple[str, str]:
        """手动创建（F9.1，manual=True → 不走自动清理）。"""
        from ..worktree.types import Worktree  # noqa: F401

        wt = await self._manager.create(name, "HEAD", manual=True)
        return wt.path, wt.branch

    def list(self) -> list[WorktreeSummary]:
        """列出 worktree（F9.2），标记当前活跃。"""
        active_path = ""
        sess = self._manager.current_session()
        if sess is not None:
            active_path = sess.worktree_path
        rows = []
        for wt in self._manager.list():
            rows.append(
                WorktreeSummary(
                    name=wt.name,
                    path=wt.path,
                    branch=wt.branch,
                    active=(wt.path == active_path),
                    manual=wt.manual,
                )
            )
        return rows

    async def enter(self, name: str) -> None:
        """进入 worktree 并把 active_cwd 指向它（F9.3）。"""
        session = await self._manager.enter(name)
        self._set_active_cwd(session.worktree_path)

    async def exit(self, action: str, discard: bool) -> bool:
        """退出当前 session（F9.4）；REMOVE 走变更保护，成功切回主工作树。"""
        from ..worktree.types import ExitAction, ExitOptions

        sess = self._manager.current_session()
        if sess is None:
            raise RuntimeError("当前没有活跃的 worktree")
        act = ExitAction.REMOVE if action == "remove" else ExitAction.KEEP
        report = await self._manager.exit(
            sess.worktree_name, act, ExitOptions(discard_changes=discard)
        )
        self._set_active_cwd("")
        return report.removed

    async def remove(self, name: str, discard: bool) -> None:
        """删除指定 worktree（F9.5）；若删的是当前 session，切回主工作树。"""
        from ..worktree.types import ExitOptions

        # remove 会清空 current_session，需先捕获是否为活跃 worktree
        sess = self._manager.current_session()
        was_active = sess is not None and sess.worktree_name == name
        await self._manager.remove(name, ExitOptions(discard_changes=discard))
        if was_active:
            self._set_active_cwd("")
