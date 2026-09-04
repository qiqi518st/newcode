"""TaskUpdate 工具（ch15 F29/F7.6）：更新共享任务（含依赖双向维护）。"""

from __future__ import annotations

from ...provider.base import ToolResult
from ..tasks import Patch, Status, Store
from . import _resolve_team, _team_error, _team_json


class TeamTaskUpdateTool:
    """TaskUpdate：更新任务状态/字段/依赖（F29，双向维护）。"""

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    @property
    def name(self) -> str:
        return "TaskUpdate"

    @property
    def description(self) -> str:
        return "更新团队共享任务（状态/负责人/依赖 add_blocks/add_blocked_by 等）"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID（必填）"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "blocked"],
                },
                "assignee": {"type": "string"},
                "add_blocks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "本任务阻塞的 task_id（双向维护）",
                },
                "add_blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "阻塞本任务的 task_id（双向维护）",
                },
                "remove_blocks": {"type": "array", "items": {"type": "string"}},
                "remove_blocked_by": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task_id"],
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
            return _team_error("当前无团队上下文")
        task_id = str(arguments.get("task_id") or "").strip()
        if not task_id:
            return _team_error("task_id 必填")

        def _list(key: str) -> list[str]:
            v = arguments.get(key) or []
            return [str(x) for x in v] if isinstance(v, list) else []

        status_raw = str(arguments.get("status") or "").strip()
        patch = Patch(
            title=str(arguments["title"]) if arguments.get("title") else None,
            description=(
                str(arguments["description"]) if arguments.get("description") else None
            ),
            status=Status(status_raw) if status_raw else None,
            assignee=str(arguments["assignee"]) if arguments.get("assignee") else None,
            add_blocks=_list("add_blocks"),
            add_blocked_by=_list("add_blocked_by"),
            remove_blocks=_list("remove_blocks"),
            remove_blocked_by=_list("remove_blocked_by"),
        )
        store = Store(team.tasks_path)
        if not await store.update(task_id, patch):
            return _team_error(f"任务不存在: {task_id}")
        return _team_json({"task_id": task_id, "updated": True})
