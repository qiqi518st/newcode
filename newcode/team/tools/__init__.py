"""团队工具（ch15 F7/F20-F34）：TeamCreate/TeamDelete（顶层，恒注册）+ 5 个协作工具。

协作工具仅在团队上下文注入（队员工具池 + Lead 团队态）；主 Agent 非团队态与普通
子 Agent 不可见（filter GLOBAL_DENY + spawn extra_tools，N2）。
"""

from __future__ import annotations

import json

from ...agent.team_hook import current_teammate
from ...provider.base import ToolResult


def _resolve_team(mgr):
    """协作工具解析当前团队：队员上下文优先，否则 Lead 的 active team（F7）。"""
    tc = current_teammate()
    if tc is not None and tc.team_name:
        return mgr.get(tc.team_name)
    return mgr.active_team()


def _team_json(data) -> ToolResult:
    return ToolResult(status="ok", output=json.dumps(data, ensure_ascii=False))


def _team_error(msg: str) -> ToolResult:
    return ToolResult(status="error", error=msg)


# ── 工具工厂（cli 装配用）────────────────────────────────
def new_team_create_tool(mgr, on_team_created=None):
    from .team_create import TeamCreateTool

    return TeamCreateTool(mgr, on_team_created)


def new_team_delete_tool(mgr, on_team_deleted=None):
    from .team_delete import TeamDeleteTool

    return TeamDeleteTool(mgr, on_team_deleted)


def new_task_create_tool(mgr):
    from .task_create import TeamTaskCreateTool

    return TeamTaskCreateTool(mgr)


def new_task_get_tool(mgr):
    from .task_get import TeamTaskGetTool

    return TeamTaskGetTool(mgr)


def new_task_list_tool(mgr):
    from .task_list import TeamTaskListTool

    return TeamTaskListTool(mgr)


def new_task_update_tool(mgr):
    from .task_update import TeamTaskUpdateTool

    return TeamTaskUpdateTool(mgr)


def new_send_message_tool(mgr):
    from .send_message import TeamSendMessageTool

    return TeamSendMessageTool(mgr)


__all__ = [
    "new_send_message_tool",
    "new_task_create_tool",
    "new_task_get_tool",
    "new_task_list_tool",
    "new_task_update_tool",
    "new_team_create_tool",
    "new_team_delete_tool",
]
