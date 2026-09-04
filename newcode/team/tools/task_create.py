"""TaskCreate 工具（ch15 F26/F7.3）：创建共享任务（团队上下文）。"""

from __future__ import annotations

from ...provider.base import ToolResult
from ..tasks import Store, Task
from . import _resolve_team, _team_error, _team_json


class TeamTaskCreateTool:
    """TaskCreate：建任务，支持 blocked_by 依赖（F26）。"""

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    @property
    def name(self) -> str:
        return "TaskCreate"

    @property
    def description(self) -> str:
        return "在团队共享任务列表创建任务（含依赖 blocked_by）"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任务标题（必填）"},
                "description": {"type": "string", "description": "任务描述（可选）"},
                "assignee": {"type": "string", "description": "负责人（队员名，可选）"},
                "blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "被哪些任务阻塞（task_id 列表，可选）",
                },
            },
            "required": ["title"],
        }

    @property
    def read_only(self) -> bool:
        return False

    @property
    def is_system(self) -> bool:
        return False

    async def execute(self, arguments: dict) -> ToolResult:
        team = _resolve_team(self._mgr)
        if team is None:
            return _team_error("当前无团队上下文（先 TeamCreate 或作为队员执行）")
        title = str(arguments.get("title") or "").strip()
        if not title:
            return _team_error("title 必填")
        store = Store(team.tasks_path)
        t = Task(
            title=title,
            description=str(arguments.get("description") or ""),
            assignee=str(arguments.get("assignee") or ""),
            blocked_by=[str(x) for x in (arguments.get("blocked_by") or [])],
        )
        task_id = await store.create(t)
        return _team_json({"task_id": task_id})
