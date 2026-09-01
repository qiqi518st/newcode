"""ch13 SubAgent 统一底座：定义 / 加载 / Fork / 启动 / 后台管理。

对外主入口：load_catalog(project_root, agents_cfg) -> Catalog（catalog.py，后续任务）；
本包不反向依赖 agent/tools（subagent.launcher → agent 单向，plan 技术决策）。
"""

from .errors import MaxTurnsReached
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
    "NOTIFICATION_XML",
    "RESULT_TRUNCATE_CHARS",
    "AgentDefinition",
    "DefinitionParseError",
    "MaxTurnsReached",
    "Source",
]
