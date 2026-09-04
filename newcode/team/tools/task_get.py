"""TaskGet 工具（ch15 F27/F7.4）：查看共享任务详情。"""

from __future__ import annotations

from ...provider.base import ToolResult
from ..tasks import Store
from . import _resolve_team, _team_error, _team_json


class TeamTaskGetTool:
    """TaskGet：按 task_id 返回任务详情（F27）。"""

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    @property
    def name(self) -> str:
        return "TaskGet"

    @property
    def description(self) -> str:
        return "按 task_id 查看团队共享任务详情"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "任务 ID"}},
            "required": ["task_id"],
        }

    @property
    def read_only(self) -> bool:
        return True

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
        store = Store(team.tasks_path)
        t = await store.get(task_id)
        if t is None:
            return _team_error(f"任务不存在: {task_id}")
        return _team_json(t.to_dict())
