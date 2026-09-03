"""TeamDelete 工具（ch15 F22/F23）：删除 Team（顶层工具，恒注册）。"""

from __future__ import annotations

import sys

from ...provider.base import ToolResult
from ..types import TeamError
from . import _team_json


class TeamDeleteTool:
    """TeamDelete：删除团队（F22）；非 force 有活跃成员拒绝（F7）。"""

    def __init__(self, mgr, on_team_deleted=None) -> None:
        self._mgr = mgr
        self._on_team_deleted = on_team_deleted  # TD-2：删队后注销 collab 工具

    @property
    def name(self) -> str:
        return "TeamDelete"

    @property
    def description(self) -> str:
        return "删除一个 AgentTeam（含成员清理）；非 force 时有活跃成员会拒绝"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "team_name": {"type": "string", "description": "团队名（必填）"},
                "force": {
                    "type": "boolean",
                    "description": "true=强制删除（忽略活跃成员）",
                },
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
        force = bool(arguments.get("force") or False)
        try:
            await self._mgr.delete(team_name, force=force)
        except TeamError as exc:
            return ToolResult(status="error", error=str(exc))
        if self._on_team_deleted is not None:
            try:
                self._on_team_deleted()
            except Exception:  # noqa: BLE001, S110 —— 失败仅记录语义，pass
                pass
        # ch15 收尾 F3.4：删队成功后补扫孤儿 team worktree（失败不阻断返回，N2）
        try:
            await self._mgr.sweep_orphan_worktrees()
        except Exception as exc:  # noqa: BLE001 —— 补扫失败仅记录
            print(f"team: 删队后孤儿清扫失败: {exc}", file=sys.stderr)
        return _team_json({"team_name": team_name, "deleted": True})
