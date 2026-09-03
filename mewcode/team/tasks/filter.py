"""共享任务列表类型（ch15 F30/F26-F29）：Task / Status / Filter / Patch / is_ready。

任务字段含双向依赖（blocked_by / blocks），TaskUpdate 维护依赖关系（F7.6）。
is_ready：无未完成 blocker（F7.5）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    """任务状态（F30）。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"

    def __str__(self) -> str:
        return self.value


@dataclass
class Task:
    """一个共享任务项（F30）。"""

    title: str
    description: str = ""
    id: str = ""  # task_<6位hex；创建时由 Store 生成
    status: Status = Status.PENDING
    assignee: str = ""
    blocked_by: list[str] = field(default_factory=list)  # 被谁阻塞
    blocks: list[str] = field(default_factory=list)  # 阻塞谁
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "assignee": self.assignee,
            "blocked_by": list(self.blocked_by),
            "blocks": list(self.blocks),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Task:
        return cls(
            id=str(raw.get("id", "")),
            title=str(raw.get("title", "")),
            description=str(raw.get("description", "")),
            status=Status(raw.get("status", Status.PENDING.value)),
            assignee=str(raw.get("assignee", "")),
            blocked_by=[str(x) for x in raw.get("blocked_by", []) or []],
            blocks=[str(x) for x in raw.get("blocks", []) or []],
            created_at=int(raw.get("created_at", 0) or 0),
            updated_at=int(raw.get("updated_at", 0) or 0),
        )


@dataclass
class Filter:
    """TaskList 过滤（F7.5）：status 过滤。"""

    status: Status | None = None

    def matches(self, t: Task) -> bool:
        if self.status is None:
            return True
        return t.status == self.status


@dataclass
class Patch:
    """TaskUpdate 补丁（F7.6）：字段更新 + 依赖双向维护。"""

    title: str | None = None
    description: str | None = None
    status: Status | None = None
    assignee: str | None = None
    add_blocks: list[str] = field(default_factory=list)
    remove_blocks: list[str] = field(default_factory=list)
    add_blocked_by: list[str] = field(default_factory=list)
    remove_blocked_by: list[str] = field(default_factory=list)


def is_ready(task: Task, all_tasks: dict[str, Task]) -> bool:
    """is_ready：blocked_by 全部 completed（F7.5）。"""
    for dep_id in task.blocked_by:
        dep = all_tasks.get(dep_id)
        if dep is None or dep.status != Status.COMPLETED:
            return False
    return True
