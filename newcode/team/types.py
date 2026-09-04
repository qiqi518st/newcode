"""AgentTeam 数据结构（ch15）：Team / TeammateInfo / BackendType / 异常家族。

- Team：长期存在的小组对象（F1.1），成员花名册 + 派生路径；`_lock` 保护状态变更（N4）
- TeammateInfo：成员花名册每条记录（F1.2）；`is_active` 保留 None 语义
  （None/True 活跃、False 空闲、终止后从 members 移除）；手写 to_dict/from_dict
  控制 `is_active` 序列化（F19c 跨进程 reload 需要细粒度控制）
- BackendType：三种执行后端（F2.1）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class BackendType(str, Enum):
    """三种执行后端（F2.1）。str,Enum 兼容 Python 3.10（StrEnum 需 3.11+）。"""

    TMUX = "tmux"
    ITERM2 = "iterm2"
    IN_PROCESS = "in-process"

    def __str__(self) -> str:
        return self.value


@dataclass
class TeammateInfo:
    """成员花名册条目（F1.2）。"""

    name: str  # 队员名（Team 内唯一，SendMessage 寻址）
    agent_id: str  # agent-a<hex> 或 "lead"
    agent_type: str = ""  # subagent 定义名；"" 表 Fork 路径
    model: str = ""  # 模型覆盖；"" 表 inherit
    worktree_path: str = ""  # 绝对路径
    branch: str = ""  # 对应 worktree 分支名
    backend_type: BackendType = BackendType.IN_PROCESS
    pane_id: str = ""  # tmux pane / iterm2 split id；in-process 空
    is_active: bool | None = None  # None/True 活跃；False 空闲；终止后移除
    plan_mode_required: bool = False
    session_dir: str = ""  # 队员独立 session 目录绝对路径

    def to_dict(self) -> dict[str, Any]:
        """序列化为 config.json 条目（is_active 的 None 语义原样保留）。"""
        return {
            "name": self.name,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "model": self.model,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "backend_type": self.backend_type.value,
            "pane_id": self.pane_id,
            "is_active": self.is_active,
            "plan_mode_required": self.plan_mode_required,
            "session_dir": self.session_dir,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TeammateInfo:
        """反序列化；缺省字段容错回退。"""
        return cls(
            name=str(raw.get("name", "")),
            agent_id=str(raw.get("agent_id", "")),
            agent_type=str(raw.get("agent_type", "")),
            model=str(raw.get("model", "")),
            worktree_path=str(raw.get("worktree_path", "")),
            branch=str(raw.get("branch", "")),
            backend_type=BackendType(
                raw.get("backend_type", BackendType.IN_PROCESS.value)
            ),
            pane_id=str(raw.get("pane_id", "")),
            is_active=raw.get("is_active"),
            plan_mode_required=bool(raw.get("plan_mode_required", False)),
            session_dir=str(raw.get("session_dir", "")),
        )


@dataclass
class Team:
    """长期存在的小组对象（F1.1）。"""

    name: str  # 原始名
    sanitized_name: str  # sanitize 后用于路径，Team 主键
    lead_agent_id: str  # 本期固定 "lead"（Lead = 主 Agent）
    backend: BackendType  # 全 team 默认后端；可被 member 覆盖
    description: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    members: list[TeammateInfo] = field(default_factory=list)

    # 派生路径（不持久化）
    config_dir: str = ""
    config_path: str = ""  # <config_dir>/config.json
    tasks_path: str = ""  # <config_dir>/tasks.json
    mailbox_dir: str = ""  # <config_dir>/mailbox/

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """序列化 config.json（F63）。"""
        return {
            "name": self.name,
            "sanitized_name": self.sanitized_name,
            "lead_agent_id": self.lead_agent_id,
            "backend": self.backend.value,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "members": [m.to_dict() for m in self.members],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Team:
        """反序列化；缺省字段容错回退（坏字段不阻断启动，F1.4）。"""
        created = datetime.now(timezone.utc)
        created_raw = raw.get("created_at", "")
        if isinstance(created_raw, str):
            try:
                created = datetime.fromisoformat(created_raw)
            except ValueError:
                created = datetime.now(timezone.utc)
        return cls(
            name=str(raw.get("name", "")),
            sanitized_name=str(raw.get("sanitized_name", "")),
            lead_agent_id=str(raw.get("lead_agent_id", "lead")),
            backend=BackendType(raw.get("backend", BackendType.IN_PROCESS.value)),
            description=str(raw.get("description", "")),
            created_at=created,
            members=[
                TeammateInfo.from_dict(m)
                for m in raw.get("members", [])
                if isinstance(m, dict)
            ],
        )

    # ── 查询辅助 ─────────────────────────────────────────
    def member_by_name(self, name: str) -> TeammateInfo | None:
        return next((m for m in self.members if m.name == name), None)

    def member_by_agent_id(self, agent_id: str) -> TeammateInfo | None:
        return next((m for m in self.members if m.agent_id == agent_id), None)


# ── 异常家族（调用方可 except 判别）──────────────────────
class TeamError(Exception):
    """团队错误基类。"""


class TeamNotFoundError(TeamError):
    """目标团队不存在（F7/Manager.get）。"""


class TeamHasActiveMembersError(TeamError):
    """删除团队时有活跃成员（F7，非 force 拒绝）。"""


class MemberExistsError(TeamError):
    """成员名在 Team 内已存在（F8）。"""


class MemberNotFoundError(TeamError):
    """成员不存在（set_member_active/remove_member 等）。"""


class InProcessTeammateNoSpawnError(TeamError):
    """in-process 队员不允许再 spawn 队员（F10.1 权限拦截）。"""


class BackendUnavailableError(TeamError):
    """执行后端不可用（F2.4 显式指定不静默降级）。"""


class SendMessageValidationError(TeamError):
    """SendMessage 参数/权限校验失败（F34）。"""
