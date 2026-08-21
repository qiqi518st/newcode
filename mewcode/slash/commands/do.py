"""/do（F8.4）：执行计划——/do <slug> 直接执行，无参弹计划列表选择。"""

from __future__ import annotations

from ...plans.manager import PlanMeta
from ..context import CommandContext
from ..registry import CommandDef, CommandKind


async def _execute(ctx: CommandContext, meta: PlanMeta, content: str) -> None:
    """打印 plan 信息后以 execute 模式执行（存量 _run_plan_execution 语义）。"""
    status = "已执行" if meta.executed else "未执行"
    ctx.ui.show_message(f"▶ 执行计划: {meta.file}", style="bold cyan")
    ctx.ui.show_message(f"  创建时间: {meta.created_at}", style="dim")
    ctx.ui.show_message(f"  状态: {status}", style="dim")
    await ctx.ui.run_agent("", "execute", plan_content=content, execute_slug=meta.slug)


async def handle_do(ctx: CommandContext, args: str) -> None:
    slug_arg = args.strip()
    if slug_arg:
        meta = ctx.plan_manager.get_plan(slug_arg)
        if meta is None:
            ctx.ui.show_message(f"未找到计划: {slug_arg}", style="red")
            return
        content = ctx.plan_manager.read_plan_content(slug_arg)
        if not content:
            ctx.ui.show_message(f"计划文件为空: {slug_arg}", style="red")
            return
        await _execute(ctx, meta, content)
        return

    # 无参：内联列出所有 plan 供选择
    plans = ctx.plan_manager.list_plans()
    if not plans:
        ctx.ui.show_message("没有已保存的计划。", style="yellow")
        return
    options = [
        (
            p.slug,
            (
                f"{i}. {p.slug} — {p.task[:30]} "
                f"[{'已执行' if p.executed else '待执行'}] ({p.created_at[:10]})"
            ),
        )
        for i, p in enumerate(plans, 1)
    ]
    slug = await ctx.ui.choose("选择要执行的计划：\n", options)
    if slug is None:
        ctx.ui.show_message("已取消", style="dim")
        return
    meta = ctx.plan_manager.get_plan(slug)
    if meta is None:
        ctx.ui.show_message(f"未找到计划: {slug}", style="red")
        return
    content = ctx.plan_manager.read_plan_content(slug)
    if not content:
        ctx.ui.show_message(f"计划文件为空: {slug}", style="red")
        return
    await _execute(ctx, meta, content)


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="do",
            kind=CommandKind.UI,
            description="执行计划（/do <slug> 或省略以选择）",
            handler=handle_do,
            usage="/do [<slug>]",
            arg_prompt="[<slug>]",
        )
    ]