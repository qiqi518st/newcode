"""/memory 家族（F8.10/F8.14-F8.16）：只读列文件名 / 详情 / 新增 / 清空。"""

from __future__ import annotations

import re

from ...memory.models import TYPE_SCOPE, MemoryOperation
from ..context import CommandContext
from ..registry import CommandDef, CommandKind

_USAGE_ADD = "/memory_add <类型> <内容>"  # 类型 ∈ user_preference/correction_feedback/project_knowledge/reference_material


def _slugify(text: str) -> str:
    """从内容首词生成记忆文件名 slug；非法/空回退 'note'。"""
    first = (text.split() or ["note"])[0] if text else "note"
    slug = re.sub(r"[^a-z0-9]+", "-", first.lower()).strip("-")[:40]
    return slug or "note"


async def handle_memory(ctx: CommandContext, _args: str) -> None:
    """F8.10：只列文件名清单（不展开内容、无编辑入口、不触发重载）。"""
    files = ctx.ui.query_memory_files()
    if not files:
        ctx.ui.show_message("无已加载的记忆文件", style="dim")
        return
    ctx.ui.show_message("\n".join(files))


async def handle_memory_list(ctx: CommandContext, _args: str) -> None:
    """F8.14：列条目详情（项目层与用户层）。"""
    m = ctx.memory_manager
    if m is None:
        ctx.ui.show_message("当前未启用记忆系统", style="yellow")
        return
    notes = m.list_notes()
    if not notes:
        ctx.ui.show_message("（暂无记忆）", style="dim")
        return
    rows = [f"({n.scope}) {n.filename} — {n.title} ({n.type})" for n in notes]
    ctx.ui.show_message("\n".join(rows))


async def handle_memory_add(ctx: CommandContext, args: str) -> None:
    """F8.15：手动添加一条记忆。"""
    m = ctx.memory_manager
    if m is None:
        ctx.ui.show_message("当前未启用记忆系统", style="yellow")
        return
    parts = args.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        ctx.ui.show_message(f"用法: {_USAGE_ADD}", style="yellow")
        return
    mtype, content = parts[0], parts[1].strip()
    level = TYPE_SCOPE.get(mtype)
    if level is None:
        valid = "/".join(TYPE_SCOPE)
        ctx.ui.show_message(f"未知记忆类型: {mtype}（可用: {valid}）", style="yellow")
        return
    store = m.project_store if level == "project" else m.user_store
    op = MemoryOperation(
        action="create",
        level=level,
        type=mtype,
        slug=_slugify(content),
        title=content[:50],
        content=content,
    )
    try:
        note = store.apply(op)
    except ValueError as exc:
        ctx.ui.show_message(str(exc), style="yellow")
        return
    ctx.ui.show_message(f"已添加记忆: {note.filename} ({level})", style="green")


async def handle_memory_clear(ctx: CommandContext, args: str) -> None:
    """F8.16：清空该作用域（user/project/空=全部）记忆，沿用 MemoryStore.clear()。"""
    m = ctx.memory_manager
    if m is None:
        ctx.ui.show_message("当前未启用记忆系统", style="yellow")
        return
    scope = args.strip()
    if scope and scope not in ("user", "project"):
        ctx.ui.show_message("用法: /memory_clear [user|project]", style="yellow")
        return
    removed = m.clear(scope)
    ctx.ui.show_message(f"已清空 {removed} 条记忆" if removed else "（无记忆可清空）", style="green")


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="memory",
            kind=CommandKind.LOCAL,
            description="列出已加载的记忆文件",
            handler=handle_memory,
        ),
        CommandDef(
            name="memory_list",
            kind=CommandKind.LOCAL,
            description="列出记忆条目详情",
            handler=handle_memory_list,
        ),
        CommandDef(
            name="memory_add",
            kind=CommandKind.LOCAL,
            description="手动添加一条记忆",
            handler=handle_memory_add,
            usage=_USAGE_ADD,
            arg_prompt="<类型> <内容>",
        ),
        CommandDef(
            name="memory_clear",
            kind=CommandKind.LOCAL,
            description="清空该作用域全部记忆",
            handler=handle_memory_clear,
            usage="/memory_clear [user|project]",
            arg_prompt="[user|project]",
        ),
    ]