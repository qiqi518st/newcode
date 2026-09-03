"""团队提示词文案（ch15 F10.9/F10.10/F11.2/F11.3）。

- build_team_context：`<team-context>` initial reminder（F10.10，spawn 时注入成员 conv）
- teammate_prompt_appendix：队员系统提示词附录（F10.9，固定文本，无变量）
- build_plan_mode_reminder：plan_mode_required 队员的门控提示（F7.4 提示层）
- team_update_message：Lead 侧 `<team-update>` reminder（F11.3，8000 截断、完整报告透传）
"""

from __future__ import annotations

from ..provider.base import Message

TEAM_UPDATE_TRUNCATE_CHARS: int = 8000

TEAMMATE_PROMPT_APPENDIX = (
    "IMPORTANT: You are running as an agent in a team.\n"
    "Just writing a response in text is not visible to others\n"
    "on your team - you MUST use the SendMessage tool.\n"
    "The user interacts primarily with the team lead.\n"
    "Your work is coordinated through the task system\n"
    "and teammate messaging."
)


def build_team_context(
    team_name: str,
    member_name: str,
    agent_id: str,
    worktree_path: str,
    members: list[str],
) -> str:
    """`<team-context>` initial reminder（F10.10）。"""
    roster = ", ".join(members) if members else "-"
    return (
        "<team-context>\n"
        f"team: {team_name}\n"
        f"你的成员名: {member_name}\n"
        f"你的 agent_id: {agent_id}\n"
        f"worktree 目录: {worktree_path}\n"
        f"当前团队成员: {roster}\n"
        "</team-context>"
    )


def build_plan_mode_reminder() -> str:
    """plan_mode_required 队员的门控提示（F7.4 提示层；硬门控由 allowed_tools 承担）。"""
    return (
        "<team-plan-mode>\n"
        "你在 plan 模式：先用只读工具调查，产出结构化计划，"
        '通过 SendMessage(to="lead", summary="plan ready", content=计划文本) 发给 Lead 等待批准。'
        "批准前不要修改任何文件（写工具当前不可用）。\n"
        "</team-plan-mode>"
    )


def team_update_message(
    team_name: str, updates: list[str], truncate_chars: int = TEAM_UPDATE_TRUNCATE_CHARS
) -> Message:
    """Lead 侧 `<team-update>` reminder（F11.3，截断上限，完整报告透传）。"""
    body = "\n".join(updates)
    if len(body) > truncate_chars:
        body = body[:truncate_chars] + "…（已截断）"
    text = f"<team-update>\nteam: {team_name}\n队员发来新消息:\n{body}\n</team-update>"
    return Message(role="user", content=text)
