"""Worktree 核心数据结构（ch14 F2.1/F2.2/F5/F6）：Worktree / 枚举 / 选项 / 报告 / 异常。

- Worktree：一个 worktree 的元信息（创建后不可变，active 映射持有）
- ExitAction / ExitOptions / ExitReport：exit/remove 的语义参数与结果
- AutoCleanupReport：auto_cleanup 的保留/清除结果
- 异常层级：WorktreeError 基类 → 具体类型（撞名 / 不存在 / 变更保护 / git 失败）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


@dataclass
class Worktree:
    """一个 Worktree 的元信息（spec F2.1）。"""

    name: str  # 原始 slug（可含 /）
    path: str  # 绝对路径 <repo_root>/.newcode/worktrees/<flat_slug>
    branch: str  # worktree-<flat_slug>
    based_on: str  # 创建时 base 引用（"HEAD" 或具体 commit）
    head_commit: str  # 创建时 commit SHA
    created: datetime
    manual: bool  # True=用户手动创建（auto_cleanup 跳过）


class ExitAction(str, Enum):
    """exit 语义：保留 or 删除（spec F5.2）。"""

    KEEP = "keep"
    REMOVE = "remove"


@dataclass
class ExitOptions:
    """exit/remove 选项；discard_changes=True 跳过变更保护（F5.2/F5.3）。"""

    discard_changes: bool = False


@dataclass
class ExitReport:
    """exit/remove 的结果（F5.2）。"""

    removed: bool
    path: str
    branch: str


@dataclass
class AutoCleanupReport:
    """auto_cleanup 的结果（F6.1/F6.2）；kept=True 时带 path/branch 供追加保留通知。"""

    kept: bool
    path: str = ""
    branch: str = ""


class WorktreeError(Exception):
    """Worktree 操作错误基类（N6 错误隔离：调用方捕获不崩主流程）。"""


class WorktreeExistsError(WorktreeError):
    """create 撞名（F3.1.2）。"""


class WorktreeNotFoundError(WorktreeError):
    """引用的 worktree 不存在（F5.1）。"""


class WorktreeHasChangesError(WorktreeError):
    """worktree 有未提交修改或本地多于 base 的 commit，拒绝删除（F5.2 变更保护）。"""


class WorktreeGitError(WorktreeError):
    """git 子进程失败，携带命令与 stderr（N10 可诊断）。"""

    def __init__(self, args: list[str], stderr: str = "") -> None:
        self.cmd_args = list(args)
        self.stderr = stderr
        super().__init__(f"git {' '.join(args)} 失败: {stderr.strip()}")
