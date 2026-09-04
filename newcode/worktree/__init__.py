"""Worktree 管理（ch14）：Manager / 类型 / slug 校验 / session / notice / 异常。

导出供 main.py 装配、AgentTool/agent_worktree、slash 适配器使用。
"""

from __future__ import annotations

from .config import WorktreesConfig, load_worktree_config
from .manager import Manager
from .notice import build_worktree_notice
from .session import WorktreeSession, clear_session, load_session, save_session
from .slug import (
    branch_name,
    flat_slug,
    is_auto_name,
    random_agent_name,
    validate_slug,
)
from .types import (
    AutoCleanupReport,
    ExitAction,
    ExitOptions,
    ExitReport,
    Worktree,
    WorktreeError,
    WorktreeExistsError,
    WorktreeGitError,
    WorktreeHasChangesError,
    WorktreeNotFoundError,
)

__all__ = [
    "AutoCleanupReport",
    "ExitAction",
    "ExitOptions",
    "ExitReport",
    "Manager",
    "Worktree",
    "WorktreeError",
    "WorktreeExistsError",
    "WorktreeGitError",
    "WorktreeHasChangesError",
    "WorktreeNotFoundError",
    "WorktreeSession",
    "WorktreesConfig",
    "branch_name",
    "build_worktree_notice",
    "clear_session",
    "flat_slug",
    "is_auto_name",
    "load_session",
    "load_worktree_config",
    "random_agent_name",
    "save_session",
    "validate_slug",
]
