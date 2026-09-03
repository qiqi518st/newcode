"""AgentTeam 统一底座（ch15）：Team / TeammateInfo / BackendType / 异常。

对外主入口：Manager（manager.py，后续任务）；本包不反向依赖 agent/tools 业务层
（agent 经 TeamHook Protocol 注入，避免环，plan TD-12）。
"""

from .types import (
    BackendType,
    BackendUnavailableError,
    InProcessTeammateNoSpawnError,
    MemberExistsError,
    MemberNotFoundError,
    SendMessageValidationError,
    Team,
    TeamError,
    TeamHasActiveMembersError,
    TeammateInfo,
    TeamNotFoundError,
)

__all__ = [
    "BackendType",
    "BackendUnavailableError",
    "InProcessTeammateNoSpawnError",
    "MemberExistsError",
    "MemberNotFoundError",
    "SendMessageValidationError",
    "Team",
    "TeamError",
    "TeamHasActiveMembersError",
    "TeamNotFoundError",
    "TeammateInfo",
]
