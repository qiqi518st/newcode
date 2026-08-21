"""命令上下文（F5）：打包命令执行所需的全部资源。

字段均有默认值（可为 None / 降级），便于单测与部分接线场景下构造。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent.agent import Agent
    from ..conversation.manager import ConversationManager
    from ..memory.manager import MemoryManager
    from ..permission.checker import PermissionChecker
    from ..plans.manager import PlanManager
    from ..session.archive import SessionArchive
    from ..session.runtime import SessionRuntime
    from .registry import CommandRegistry
    from .ui import UIController


@dataclass
class CommandContext:
    """命令执行环境：handler(ctx, args) 中命令所需的一切资源。

    各字段在 main.py 组装一次；未接线的字段保持 None，命令实现做空值防御。
    """

    registry: CommandRegistry
    ui: UIController
    agent: Agent
    conversation: ConversationManager
    plan_manager: PlanManager
    session_runtime: SessionRuntime | None = None
    session_archive: SessionArchive | None = None
    memory_manager: MemoryManager | None = None
    permission: PermissionChecker | None = None
    version: str = ""
    cwd: str = "."
    # ch11 Skill 依赖（main.py 装配注入；未接线保持 None，handler 做空值防御）
    catalog: object | None = None  # skills.Catalog
    active_skills: object | None = None  # skills.ActiveSkills
    executor: object | None = None  # skills.Executor
