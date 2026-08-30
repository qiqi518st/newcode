"""/legacy 存量命令（F8.1/F8.23-F8.25）：/exit、/quit（别名）、/resume（隐藏）、/delete-plan。"""

from __future__ import annotations

from ..context import CommandContext
from ..registry import CommandDef, CommandKind


async def handle_exit(ctx: CommandContext, _args: str) -> None:
    """F8.1：退出——request_exit 先取消主 cancel scope（N12），REPL 主循环收到退出标志后 break。"""
    ctx.ui.request_exit()
    ctx.ui.show_message("再见！")


async def handle_resume(ctx: CommandContext, _args: str) -> None:
    """F8.25：/resume 作为 /session_resume 的隐藏别名（无参，打开历史会话列表选择恢复，ch09 兼容）。"""
    await ctx.ui.request_session_list()


async def handle_delete_plan(ctx: CommandContext, _args: str) -> None:
    """F8.23：删除计划（存量迁移，内联多选 + 二次确认，默认取消防误触）。"""
    plans = ctx.plan_manager.list_plans()
    if not plans:
        ctx.ui.show_message("没有可删除的计划。", style="yellow")
        return
    options = [
        (
            p.slug,
            f"{p.slug} — {p.task[:30]} [{'已执行' if p.executed else '待执行'}] ({p.created_at[:10]})",
        )
        for p in plans
    ]
    result = await ctx.ui.choose_multi("选择要删除的计划：\n", options)
    if result is None:
        ctx.ui.show_message("已取消", style="dim")
        return
    if not result:
        ctx.ui.show_message("未选中任何计划", style="yellow")
        return
    confirm = await ctx.ui.choose(
        f"确认删除 {len(result)} 个计划？\n",
        [("yes", "yes — 确认删除"), ("no", "no — 取消")],
        default_index=1,  # 默认取消，防误触
    )
    if confirm != "yes":
        ctx.ui.show_message("已取消", style="dim")
        return
    ctx.plan_manager.delete_plans(result)
    ctx.ui.show_message(f"已删除 {len(result)} 个计划", style="bold green")


def build() -> list[CommandDef]:
    exit_cmd = CommandDef(
        name="exit",
        kind=CommandKind.UI,
        description="退出 MewCode",
        handler=handle_exit,
        aliases=("quit",),  # F8.24：/quit 是 /exit 的别名
    )
    resume_cmd = CommandDef(
        name="resume",
        kind=CommandKind.UI,
        description="打开历史会话列表并恢复（/session_resume 的隐藏别名）",
        handler=handle_resume,
        hidden=True,  # F10/F8.25：不出现在 /help 与补全，dispatcher 仍命中
    )
    delete_plan_cmd = CommandDef(
        name="delete-plan",
        kind=CommandKind.UI,
        description="删除计划",
        handler=handle_delete_plan,
    )
    return [exit_cmd, resume_cmd, delete_plan_cmd]
