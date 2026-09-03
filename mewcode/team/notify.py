"""团队完成通知（ch15 F9.1）：build_team_notification——`<task-notification>` 含 `<usage>`。

task-id 用队员 agent_id；usage 含 total_tokens / tool_uses / duration_ms
（in-process 复用 ch13 统计；Pane 队员由 --team-member 进程自行统计写入）。
"""

from __future__ import annotations

import time

NOTIFICATION_TEAM_XML = """<task-notification>
<task-id>{task_id}</task-id>
<status>{status}</status>
<summary>{summary}</summary>
<result>{result}</result>
<usage>
  <total_tokens>{total_tokens}</total_tokens>
  <tool_uses>{tool_uses}</tool_uses>
  <duration_ms>{duration_ms}</duration_ms>
</usage>
</task-notification>"""


def build_team_notification(
    agent_id: str,
    member_name: str,
    status: str,
    result: str,
    *,
    total_tokens: int = 0,
    tool_uses: int = 0,
    duration_ms: int = 0,
) -> str:
    """组团队格式 `<task-notification>`（F9.1，含 usage 五段）。"""
    summary = f'Agent "{member_name}" {status}'
    return NOTIFICATION_TEAM_XML.format(
        task_id=agent_id,
        status=status,
        summary=summary,
        result=result or "",
        total_tokens=total_tokens,
        tool_uses=tool_uses,
        duration_ms=int(duration_ms),
    )


def now_ms() -> int:
    """当前毫秒时间戳（duration 计算用）。"""
    return int(time.monotonic() * 1000)
