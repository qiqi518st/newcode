"""/compact（F8.5）：手动触发上下文压缩（走同一事件流推送进度）。"""

from __future__ import annotations

from ..context import CommandContext
from ..registry import CommandDef, CommandKind


async def handle_compact(ctx: CommandContext, _args: str) -> None:
    """F8.5：手动压缩——经 UIController.request_compact 走 Agent.run_force_compact 事件流。"""
    await ctx.ui.request_compact()


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="compact",
            kind=CommandKind.UI,
            description="手动触发上下文压缩",
            handler=handle_compact,
        )
    ]