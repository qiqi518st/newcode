"""/plan / /normal（F8.2/F8.3）：计划模式进入 / 退出（权限模式随 /plan 联动 PLAN）。"""

from __future__ import annotations

from ..context import CommandContext
from ..registry import CommandDef, CommandKind

_GUIDE = "已进入计划模式。请直接描述你的任务，例如：创建一个 hello.txt 文件。"


async def handle_plan(ctx: CommandContext, args: str) -> None:
    """F8.2：切到计划模式（AppMode PLAN + 权限模式 PLAN）。带参数时直接以任务起一轮 plan 回合（存量语义）。"""
    ctx.ui.set_app_mode("plan")
    ctx.ui.set_permission_mode("plan")
    task = args.strip()
    if task:
        ctx.ui.show_message("已进入计划模式。", style="bold cyan")
        await ctx.ui.run_agent(task, "plan")
    else:
        ctx.ui.show_message(_GUIDE, style="bold cyan")


async def handle_normal(ctx: CommandContext, _args: str) -> None:
    """F8.3：退出计划模式、切回普通 AppMode（存量：不动权限模式，N8）。"""
    ctx.ui.set_app_mode("normal")
    ctx.ui.show_message("已退出计划模式，回到普通模式。", style="bold cyan")


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="plan",
            kind=CommandKind.UI,
            description="进入计划模式",
            handler=handle_plan,
        ),
        CommandDef(
            name="normal",
            kind=CommandKind.UI,
            description="退出计划模式",
            handler=handle_normal,
        ),
    ]
