"""/clear（F8.7）：清空当前会话并开启新 session（原子重置）。"""

from __future__ import annotations

from ..context import CommandContext
from ..registry import CommandDef, CommandKind

_NOTICE = "已清空当前会话，开启新 session"


async def handle_clear(ctx: CommandContext, _args: str) -> None:
    """F8.7：关闭旧存档 → 新会话 → 重建会话 → compact 子状态清零 → token/回合归零 → AppMode NORMAL。

    实际重置顺序在 RichUIController.request_clear_session 内实现（plan.md「/clear 原子重置顺序」）。
    ch11（F5.5）：清空后顺带清 activeSkills，避免新对话残留上一次激活的 SOP。
    """
    await ctx.ui.request_clear_session()
    ctx.ui.clear_active_skills()
    ctx.ui.show_message(_NOTICE, style="bold cyan")


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="clear",
            kind=CommandKind.UI,
            description="清空当前会话并开启新 session",
            handler=handle_clear,
        )
    ]
