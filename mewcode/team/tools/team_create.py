"""TeamCreate 工具（ch15 F20/F21）：创建 Team（顶层工具，恒注册）。"""

from __future__ import annotations

from ...provider.base import ToolResult
from ..types import TeamError
from . import _team_json


class TeamCreateTool:
    """TeamCreate：主 Agent 调用即创建团队（F20）。"""

    def __init__(self, mgr, on_team_created=None) -> None:
        self._mgr = mgr
        self._on_team_created = (
            on_team_created  # TD-2：建队后把 collab 工具注册进主 registry
        )

    @property
    def name(self) -> str:
        return "TeamCreate"

    @property
    def description(self) -> str:
        return "创建一个长期存在的 AgentTeam，自己成为 Lead（F5/G2）"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "description": "团队名（必填）"},
                "description": {"type": "string", "description": "团队描述（可选）"},
                "agent_type": {"type": "string", "description": "保留位，本期不使用"},
            },
            "required": ["team_name"],
        }

    @property
    def read_only(self) -> bool:
        return False

    @property
    def is_system(self) -> bool:
        return False

    async def execute(self, arguments: dict) -> ToolResult:
        team_name = str(arguments.get("team_name") or "").strip()
        if not team_name:
            return ToolResult(status="error", error="team_name 必填")
        description = str(arguments.get("description") or "")
        try:
            team = await self._mgr.create(team_name, description)
        except TeamError as exc:
            return ToolResult(status="error", error=str(exc))
        if self._on_team_created is not None:
            try:
                self._on_team_created()
            except Exception:  # noqa: BLE001, S110 —— 失败仅记录语义，pass
                pass
        return _team_json(
            {
                "team_name": team.sanitized_name,
                "backend": team.backend.value,
                "config_path": team.config_path,
            }
        )
