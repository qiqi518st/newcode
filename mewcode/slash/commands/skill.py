"""/skill 管理命令（F7）：list / info / reload / load / on / off / unload。

- list：名字、说明、来源层级、启用状态、是否激活（排版对齐 `{name:<20}`）。
- info <n>：单个 Skill 详情（frontmatter 全部字段、源路径、是否激活）。
- reload [n]：无 name 全量重扫 + 同步 /名字 注册（F7.3）；有 name 重读单个源文件。
- load <n>：手动全量加载（跳过阶段一，直接阶段二激活，F7.4）。
- on <n> / off <n>：启用/禁用（F7.5/F7.6，disabled 跨会话持久 F7.8）。
- unload <n>：移出注册 + 清理内存状态 + 清 disabled 标记（F7.7）。
"""

from __future__ import annotations

from ...skills.render import render_body
from ..context import CommandContext
from ..registry import CommandDef, CommandKind

_SUBCOMMANDS = ("list", "info", "reload", "load", "on", "off", "unload")

_USAGE = "/skill <list|info|reload|load|on|off|unload> [<name>]"


def _get_ctx(ctx: CommandContext):
    """取 catalog/store/executor；未接线时返回 (None, None, None)。"""
    return (
        getattr(ctx, "catalog", None),
        getattr(ctx, "active_skills", None),
        getattr(ctx, "executor", None),
    )


async def handle_skill(ctx: CommandContext, args: str) -> None:
    """F7：按子命令分发。"""
    parts = args.split()
    sub = parts[0] if parts else ""
    if sub not in _SUBCOMMANDS:
        ctx.ui.show_message(_USAGE, style="yellow")
        return
    name = parts[1] if len(parts) > 1 else ""
    if sub in ("info", "load", "on", "off", "unload") and not name:
        ctx.ui.show_message(f"用法: /skill {sub} <name>", style="yellow")
        return

    catalog, store, executor = _get_ctx(ctx)
    if sub == "list":
        await _do_list(ctx, catalog, store)
    elif sub == "info":
        await _do_info(ctx, catalog, store, name)
    elif sub == "reload":
        await _do_reload(ctx, catalog, store, executor, name)
    elif sub == "load":
        await _do_load(ctx, catalog, store, executor, name)
    elif sub == "on":
        await _do_on(ctx, catalog, name)
    elif sub == "off":
        await _do_off(ctx, catalog, store, name)
    elif sub == "unload":
        await _do_unload(ctx, catalog, store, name)


async def _do_list(ctx: CommandContext, catalog, store) -> None:
    """F7.1：列出所有 Skill（名字/说明/来源/启用/激活）。"""
    if catalog is None:
        ctx.ui.show_message("Skill 系统未接线", style="yellow")
        return
    active = set(store.names()) if store is not None else set()
    skills = catalog.list()
    if not skills:
        ctx.ui.show_message("（无可用 Skill）", style="dim")
        return
    lines = [f"  {s.name:<20} {s.meta.description}  [{s.source.value}]" for s in skills]
    ctx.ui.show_message("\n".join(lines))
    if active:
        ctx.ui.show_message(f"已激活: {', '.join(sorted(active))}", style="green")


async def _do_info(ctx: CommandContext, catalog, store, name: str) -> None:
    """F7.2：单个 Skill 详情（frontmatter 全部字段 + 源路径 + 激活状态）。"""
    if catalog is None:
        ctx.ui.show_message("Skill 系统未接线", style="yellow")
        return
    skill = catalog.get(name)
    if skill is None:
        ctx.ui.show_message(f"未知 Skill: {name}", style="red")
        return
    active = name in (store.names() if store is not None else [])
    meta = skill.meta
    lines = [
        f"name: {meta.name}",
        f"description: {meta.description}",
        f"mode: {meta.mode}",
        f"context: {meta.fork_context}",
        f"model: {meta.model or '(session default)'}",
        f"allowedTools: {', '.join(meta.allowed_tools) or '(all)'}",
        f"source: {skill.source.value}",
        f"source_path: {skill.source_path}",
        f"tools: {', '.join(t.name for t in skill.tools) or '(none)'}",
        f"active: {active}",
    ]
    ctx.ui.show_message("\n".join(lines))


async def _do_reload(ctx, catalog, store, executor, name: str) -> None:
    """F7.3：无 name 全量重扫 + 同步 /名字 注册；有 name 重读单个源文件。"""
    if catalog is None:
        ctx.ui.show_message("Skill 系统未接线", style="yellow")
        return
    if name:
        skill = catalog.get(name)  # get 每次重读源文件（热更新）
        if skill is None:
            ctx.ui.show_message(f"未知 Skill: {name}", style="red")
            return
        ctx.ui.show_message(f"已重载 Skill: {name}", style="green")
        return
    added, removed = catalog.reload()
    if executor is not None:
        from .skill_register import register_skills_as_commands, remove_skill_commands

        remove_skill_commands(ctx.registry)
        register_skills_as_commands(ctx.registry, catalog, executor)
    msg = []
    if added:
        msg.append(f"新增: {', '.join(added)}")
    if removed:
        msg.append(f"移除: {', '.join(removed)}")
    ctx.ui.show_message(
        f"已全量重扫 Skills（{len(catalog.list())} 个）"
        + ("；" + "; ".join(msg) if msg else ""),
        style="green",
    )


async def _do_load(ctx, catalog, store, executor, name: str) -> None:
    """F7.4：手动全量加载（跳过阶段一，直接阶段二激活）。"""
    if catalog is None or store is None:
        ctx.ui.show_message("Skill 系统未接线", style="yellow")
        return
    skill = catalog.get(name)
    if skill is None:
        ctx.ui.show_message(f"未知 Skill: {name}", style="red")
        return
    body = render_body(skill, "")
    store.activate(name, body)
    ctx.ui.show_message(f"已加载 Skill: {name}", style="green")


async def _do_on(ctx: CommandContext, catalog, name: str) -> None:
    """F7.5：重新启用（从 disabled 集合移除），立即生效同步阶段一摘要。"""
    if catalog is None:
        ctx.ui.show_message("Skill 系统未接线", style="yellow")
        return
    if catalog.get(name) is None and catalog.is_disabled(name):
        ctx.ui.show_message(f"未知 Skill: {name}", style="red")
        return
    catalog.set_disabled(name, False)
    ctx.ui.show_message(f"已启用 Skill: {name}", style="green")


async def _do_off(ctx: CommandContext, catalog, store, name: str) -> None:
    """F7.6：禁用（加入 disabled；从摘要与可用列表移除；已激活立即失活）。"""
    if catalog is None:
        ctx.ui.show_message("Skill 系统未接线", style="yellow")
        return
    if catalog.get(name) is None and not catalog.is_disabled(name):
        ctx.ui.show_message(f"未知 Skill: {name}", style="red")
        return
    if store is not None:
        store.deactivate(name)
    catalog.set_disabled(name, True)
    ctx.ui.show_message(f"已禁用 Skill: {name}", style="green")


async def _do_unload(ctx: CommandContext, catalog, store, name: str) -> None:
    """F7.7：卸载（移出注册 + 清理内存状态 + 清 disabled 标记）。"""
    if catalog is None:
        ctx.ui.show_message("Skill 系统未接线", style="yellow")
        return
    if not catalog.is_disabled(name) and catalog.get(name) is None:
        ctx.ui.show_message(f"未知 Skill: {name}", style="red")
        return
    if store is not None:
        store.deactivate(name)
    catalog.remove(name)
    catalog.set_disabled(name, False)
    from .skill_register import remove_skill_commands

    remove_skill_commands(ctx.registry)
    ctx.ui.show_message(f"已卸载 Skill: {name}", style="green")


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="skill",
            kind=CommandKind.LOCAL,
            description="管理 Skills（list/info/reload/load/on/off/unload）",
            handler=handle_skill,
            usage=_USAGE,
            arg_prompt="<list|info|reload|load|on|off|unload> [<name>]",
        )
    ]
