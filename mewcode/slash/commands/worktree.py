"""/worktree 命令族（ch14 F9）：create / list / enter / exit / remove。

经 WorktreeAccessor 协议（ctx.ui.worktree_accessor()）访问 worktree 管理器，
不直接依赖 worktree 包（F9 技术决策：slash 层无反向依赖）。镜像 tasks.py 结构。
"""

from __future__ import annotations

from ..context import CommandContext
from ..registry import CommandDef, CommandKind


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="worktree",
            kind=CommandKind.LOCAL,
            description="管理 Git Worktree：create / list / enter / exit / remove",
            usage="/worktree [create <slug>|list|enter <slug>|exit [--remove] [--discard]|remove <slug> [--discard]]",
            handler=_handler,
        )
    ]


async def _handler(ctx: CommandContext, args: str) -> None:
    accessor = ctx.ui.worktree_accessor()
    if accessor is None:
        ctx.ui.show_message("Worktree 功能未启用", style="yellow")
        return

    parts = args.split(None, 1)
    sub = parts[0].strip().lower() if parts and parts[0].strip() else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "list":
        rows = accessor.list()
        if not rows:
            ctx.ui.show_message("No worktrees.")
            return
        lines = []
        for w in rows:
            mark = " [active]" if w.active else ""
            manual = " [手动]" if w.manual else ""
            lines.append(f"{w.name}  {w.path}  {w.branch}{mark}{manual}")
        ctx.ui.show_message("\n".join(lines))
        return

    if sub == "create":
        slug = rest.split()[0] if rest.split() else ""
        if not slug:
            ctx.ui.show_message("用法: /worktree create <slug>", style="yellow")
            return
        try:
            path, branch = await accessor.create(slug)
        except Exception as e:  # noqa: BLE001 - 错误隔离（N6）
            ctx.ui.show_message(f"创建失败: {e}", style="red")
            return
        ctx.ui.show_message(f"Worktree 已创建: {path}（分支 {branch}）")
        return

    if sub == "enter":
        slug = rest.split()[0] if rest.split() else ""
        if not slug:
            ctx.ui.show_message("用法: /worktree enter <slug>", style="yellow")
            return
        try:
            await accessor.enter(slug)
        except Exception as e:  # noqa: BLE001
            ctx.ui.show_message(f"进入失败: {e}", style="red")
            return
        ctx.ui.show_message(f"已进入 {slug}")
        return

    if sub == "exit":
        discard = "--discard" in rest
        remove = "--remove" in rest
        action = "remove" if remove else "keep"
        try:
            removed = await accessor.exit(action, discard)
        except Exception as e:  # noqa: BLE001
            ctx.ui.show_message(f"退出失败: {e}", style="red")
            return
        ctx.ui.show_message("已退出" + ("（worktree 已删除）" if removed else ""))
        return

    if sub == "remove":
        words = rest.split()
        slug = words[0] if words else ""
        discard = "--discard" in rest
        if not slug:
            ctx.ui.show_message(
                "用法: /worktree remove <slug> [--discard]", style="yellow"
            )
            return
        try:
            await accessor.remove(slug, discard)
        except Exception as e:  # noqa: BLE001
            ctx.ui.show_message(f"删除失败: {e}", style="red")
            return
        ctx.ui.show_message(f"已删除 {slug}")
        return

    ctx.ui.show_message(f"未知子命令: {sub}", style="yellow")
