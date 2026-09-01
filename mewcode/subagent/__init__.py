"""ch13 SubAgent 统一底座：定义 / 加载 / Fork / 启动 / 后台管理。

对外主入口：load_catalog(project_root, agents_cfg) -> Catalog（catalog.py，后续任务）；
本包不反向依赖 agent/tools（subagent.launcher → agent 单向，plan 技术决策）。
"""

from .errors import MaxTurnsReached
from .fork import (
    FORK_BOILERPLATE,
    FORK_BOILERPLATE_TAG,
    build_forked_messages,
    is_fork_context,
)
from .manager import (
    BackgroundTask,
    ForegroundHandle,
    Status,
    TaskBusy,
    TaskCapReached,
    TaskManager,
    TaskNotFound,
    build_task_notification,
)
from .types import (
    DEFAULT_MAX_TURNS,
    NOTIFICATION_XML,
    RESULT_TRUNCATE_CHARS,
    AgentDefinition,
    DefinitionParseError,
    Source,
)

__all__ = [
    "DEFAULT_MAX_TURNS",
    "FORK_BOILERPLATE",
    "FORK_BOILERPLATE_TAG",
    "NOTIFICATION_XML",
    "RESULT_TRUNCATE_CHARS",
    "AgentDefinition",
    "BackgroundTask",
    "DefinitionParseError",
    "ForegroundHandle",
    "MaxTurnsReached",
    "Source",
    "Status",
    "TaskBusy",
    "TaskCapReached",
    "TaskManager",
    "TaskNotFound",
    "build_forked_messages",
    "build_task_notification",
    "is_fork_context",
]
