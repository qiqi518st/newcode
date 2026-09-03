"""TaskList 工具（ch15 F28/F7.5）：列出共享任务（status 过滤 + is_ready）。"""

from __future__ import annotations

from ...provider.base import ToolResult
from ..tasks import Filter, Status, Store
from . import _resolve_team, _team_error, _team_json


class TeamTaskListTool:
    """TaskList：列出全部任务，带依赖标注与 is_ready（F28）。"""

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    @property
    def name(self) -> str:
        return "TaskList"

    @property
    def description(self) -> str:
        return "列出团队共享任务（status 过滤，含 blocked_by/blocks/is_ready）"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "blocked"],
                    "description": "状态过滤（可选）",
                }
            },
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
        status_raw = str(arguments.get("status") or "").strip()
        f = Filter(status=Status(status_raw)) if status_raw else Filter()
        store = Store(team.tasks_path)
        tasks = await store.list_(f)
        out = []
        for t in tasks:
            d = t.to_dict()
            d["is_ready"] = t.__dict__.get("is_ready", False)
            out.append(d)
        return _team_json(out)
