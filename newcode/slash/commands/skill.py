"""/skill 管理命令（F7，ch11 变更记录整合后）：list / info / reload / load / unload。

- list：名字、说明、来源层级、禁用状态、是否激活（排版对齐 `{name:<20}`）。
  显示**全部** Skill（含禁用的，标注 [disabled]），让用户知道有哪些被卸载/禁用。
- info <n>：单个 Skill 详情（frontmatter 全部字段、源路径、禁用/激活状态）。
- reload [n]：无 name 全量重扫 + 同步 /名字 注册（F7.3）；有 name 重读单个源文件。
- load <n>：启用 + 激活（= 原 load + 原 on）。从 disabled 集合移除（跨会话持久），
  激活 SOP 到当前会话，并恢复 /<名字> 命令注册。
- unload <n>：禁用 + 卸载（= 原 unload + 原 off）。加入 disabled 集合（跨会话持久，
  重启后仍不出现）、立即失活、删除 /<名字> 命令、清除内存缓存。
  Skill 文件保留在磁盘，/skill list 仍可见（标注 [disabled]）。
"""

from __future__ import annotations

from ...skills.render import render_body
from ..context import CommandContext
from ..registry import CommandDef, CommandKind

_SUBCOMMANDS = ("list", "info", "reload", "load", "unload")

_USAGE = "/skill <list|info|reload|load|unload> [<name>]"


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
    if sub in ("info", "load", "unload") and not name:
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
    elif sub == "unload":
        await _do_unload(ctx, catalog, store, name)


async def _do_list(ctx: CommandContext, catalog, store) -> None:
    """F7.1：列出所有 Skill（含禁用的，标注 [disabled]；名字/说明/来源/激活）。

    用 list_all() 显示全部——unload 后仍能看到被禁用的 Skill，避免「消失无痕迹」。
    """
    if catalog is None:
        ctx.ui.show_message("Skill 系统未接线", style="yellow")
        return
    active = set(store.names()) if store is not None else set()
    skills = catalog.list_all()
    if not skills:
        ctx.ui.show_message("（无 Skill）", style="dim")
        return
    lines = []
    for s in skills:
        disabled = " [disabled]" if catalog.is_disabled(s.name) else ""
        lines.append(
            f"  {s.name:<20} {s.meta.description}  [{s.source.value}]{disabled}"
        )
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
    disabled = catalog.is_disabled(name)
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
        f"disabled: {disabled}",
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
    """F7.4（ch11 变更：load = 原 load + on）：启用（持久）+ 激活 + 恢复 /名字 命令。

    从 disabled 集合移除（跨会话持久），激活 SOP 到当前会话，并重注册 /<名字> 命令。
    """
    if catalog is None or store is None:
        ctx.ui.show_message("Skill 系统未接线", style="yellow")
        return
    skill = catalog.get(name)
    if skill is None:
        ctx.ui.show_message(f"未知 Skill: {name}", style="red")
        return
    catalog.set_disabled(name, False)  # 启用（落盘 disabled.json，F7.8）
    body = render_body(skill, "")
    store.activate(name, body)
    # 恢复 /<名字> 命令（全量重注册：先清旧再按可用列表注册）
    if executor is not None:
        from .skill_register import (
            register_skills_as_commands,
            remove_skill_commands,
        )

        remove_skill_commands(ctx.registry)
        register_skills_as_commands(ctx.registry, catalog, executor)
    ctx.ui.show_message(f"已加载并启用 Skill: {name}", style="green")


async def _do_unload(ctx: CommandContext, catalog, store, name: str) -> None:
    """F7.7（ch11 变更：unload = 原 unload + off）：禁用（持久）+ 失活 + 删命令 + 清缓存。

    加入 disabled 集合（跨会话持久，重启后仍不出现），立即失活，删除 /<名字> 命令，
    清除内存缓存；Skill 文件保留在磁盘，/skill list 仍可见（标注 [disabled]）。
    """
    if catalog is None:
        ctx.ui.show_message("Skill 系统未接线", style="yellow")
        return
    if catalog.get(name) is None and not catalog.is_disabled(name):
        ctx.ui.show_message(f"未知 Skill: {name}", style="red")
        return
    if store is not None:
        store.deactivate(name)
    catalog.set_disabled(name, True)  # 禁用（落盘 disabled.json，F7.8）
    catalog.invalidate(name)  # 清内存缓存（下次 get 从磁盘重读）
    # 删除 /<名字> 命令（精确删单个，不误伤其它 [skill] 命令）
    ctx.registry.unregister(name)
    ctx.ui.show_message(f"已卸载并禁用 Skill: {name}", style="green")


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="skill",
            kind=CommandKind.LOCAL,
            description="管理 Skills（list/info/reload/load/unload）",
            handler=handle_skill,
            usage=_USAGE,
            arg_prompt="<list|info|reload|load|unload> [<name>]",
        )
    ]
