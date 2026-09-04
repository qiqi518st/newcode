"""/team 命令族（ch15 F16/F59-F62）：列出 / 详情 / 删除 / 终止队员。

user 侧管理入口（Lead 走工具 TeamCreate/TeamDelete/Agent）；经 CommandContext.team_mgr 访问。
"""

from __future__ import annotations

from ..context import CommandContext
from ..registry import CommandDef, CommandKind


async def _handler(ctx: CommandContext, args: str) -> None:
    mgr = getattr(ctx, "team_mgr", None)
    if mgr is None:
        ctx.ui.show_message("团队功能未启用", style="yellow")
        return
    parts = args.split(None, 1)
    sub = parts[0].strip().lower() if parts and parts[0].strip() else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub in ("", "list"):
        teams = mgr.list_()
        if not teams:
            ctx.ui.show_message("暂无团队（/team 或让 Lead 调 TeamCreate 创建）")
            return
        rows = []
        for t in teams:
            active = sum(
                1 for m in t.members if m.name != "lead" and m.is_active is not False
            )
            total = sum(1 for m in t.members if m.name != "lead")
            rows.append(
                f"{t.sanitized_name}  {t.backend.value}  {total} 成员  [{active}/{total}] 活跃"
            )
        ctx.ui.show_message("\n".join(rows))
        return

    if sub == "info":
        if not rest:
            ctx.ui.show_message("用法: /team info <name>", style="yellow")
            return
        team = mgr.get(rest)
        if team is None:
            ctx.ui.show_message(f"团队不存在: {rest}", style="yellow")
            return
        lines = [
            f"team: {team.sanitized_name}",
            f"backend: {team.backend.value}",
            f"config: {team.config_path}",
        ]
        for m in team.members:
            lines.append(
                f"  {m.name}  {m.agent_id}  {m.backend_type.value}  "
                f"active={m.is_active}  wt={m.worktree_path or '-'}"
            )
        ctx.ui.show_message("\n".join(lines))
        return

    if sub == "delete":
        if not rest:
            ctx.ui.show_message("用法: /team delete <name> [--force]", style="yellow")
            return
        name, _, flag = rest.partition(" ")
        force = "--force" in flag
        try:
            await mgr.delete(name, force=force)
        except Exception as exc:  # noqa: BLE001 —— 删除错误转文案
            ctx.ui.show_message(f"删除失败: {exc}", style="red")
            return
        ctx.ui.show_message(f"已删除团队 {name}", style="green")
        return

    if sub == "kill":
        if not rest:
            ctx.ui.show_message("用法: /team kill <member>", style="yellow")
            return
        found = None
        for team in mgr.list_():
            m = team.member_by_name(rest)
            if m is not None:
                found = (team, m)
                break
        if found is None:
            ctx.ui.show_message(f"成员不存在: {rest}", style="yellow")
            return
        team, member = found
        try:
            from ...team.backend import new_backend

            backend = new_backend(member.backend_type, task_mgr=ctx.task_manager)
            await backend.kill(member.pane_id, member.agent_id)
            await mgr.remove_member(team, member.name)
        except Exception as exc:  # noqa: BLE001 —— kill 错误转文案
            ctx.ui.show_message(f"终止失败: {exc}", style="red")
            return
        ctx.ui.show_message(f"已终止队员 {member.name}", style="green")
        return

    ctx.ui.show_message(
        f"未知子命令: {sub}（/team [list|info <name>|delete <name> [--force]|kill <member>]）",
        style="yellow",
    )


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="team",
            kind=CommandKind.LOCAL,
            description="团队管理：列出 / info / delete / kill",
            usage="/team | /team info <name> | /team delete <name> [--force] | /team kill <member>",
            handler=_handler,
        )
    ]
