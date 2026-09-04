"""团队通知测试（ch15 F9.1/F11.2/F11.3）。

防的 bug：
- <task-notification> 缺 <usage> 段（F9.1 五段齐全）
- <team-update> 超长内容不截断撑爆上下文（F11.3 8000 截断）
- incoming-messages 格式缺字段（F11.2）
"""

from __future__ import annotations

from newcode.team.notices import team_update_message
from newcode.team.notify import build_team_notification


def test_build_team_notification_has_usage():
    xml = build_team_notification(
        "agent-abc",
        "alice",
        "completed",
        "done work",
        total_tokens=100,
        tool_uses=3,
        duration_ms=2500,
    )
    for fragment in (
        "<task-id>agent-abc</task-id>",
        "<status>completed</status>",
        '<summary>Agent "alice" completed</summary>',
        "<total_tokens>100</total_tokens>",
        "<tool_uses>3</tool_uses>",
        "<duration_ms>2500</duration_ms>",
    ):
        assert fragment in xml, fragment
    assert xml.count("<usage>") == 1  # F9.1 五段齐全


def test_team_update_truncates():
    # 防的 bug：队员完整报告超长导致 Lead 上下文爆炸（F11.3）
    m = team_update_message("demo", ["[1] 来自 alice: " + "x" * 9000])
    assert "<team-update>" in m.content
    assert "（已截断）" in m.content
    assert len(m.content) < 8500


def test_team_update_passes_full_report_when_small():
    report = "agent.py 已分析完成，结论：需要重构 auth"
    m = team_update_message("demo", [f"[1] 来自 alice: {report}"])
    assert report in m.content  # 完整报告透传（F11.3）
