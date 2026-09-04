"""Skill 动态注册为 /<名字> 命令（F2.4/N11）。

每个 Skill 注册一条 KindUI 命令，描述标注 `[skill]` 以区分内置命令；执行时经
executor.execute 重读源文件正文（热更新）。与内置命令冲突时 `except RuntimeError
→ warning 跳过`（F2.5，内置优先；/review 迁移为唯一豁免由注册顺序保证）。
handler 按 mode 分发：inline → await executor.execute（内部注入消息触发回合）；
fork → asyncio.create_task（独立子 Agent 后台跑，结果经 append_assistant_message 回流）。

闭包陷阱：handler 用 functools.partial(handler, name=skill.name) 显式拷贝循环变量
（Python 闭包按引用绑定，循环里不拷贝会全部指向最后一个 skill）。
"""

from __future__ import annotations

import asyncio
import functools
import logging

from ..context import CommandContext
from ..registry import CommandDef, CommandKind, CommandRegistry

logger = logging.getLogger(__name__)

# 已注册 Skill 命令名集合（再注册前先清旧，供 /skill reload 与未来 InstallSkill 同步）
_REGISTERED_SKILL_NAMES: set[str] = set()


async def _handle_skill_command(ctx: CommandContext, args: str, *, name: str) -> None:
    """/<skill名> 的 handler：按 mode 分发 inline（等回合）/ fork（后台任务）。"""
    executor = getattr(ctx, "executor", None)
    if executor is None:
        ctx.ui.show_message("Skill 执行器未接线", style="yellow")
        return
    catalog = getattr(ctx, "catalog", None)
    skill = catalog.get(name) if catalog is not None else None
    if skill is None:
        ctx.ui.show_message(f"未知 Skill: {name}", style="yellow")
        return
    if skill.meta.mode == "fork":
        # fork 后台跑：create_task 不阻塞命令循环，结果写回主对话（F3.1/N13）
        asyncio.create_task(executor.execute(ctx, ctx.ui, name, args.strip()))
    else:
        await executor.execute(ctx, ctx.ui, name, args.strip())


def register_skills_as_commands(reg: CommandRegistry, catalog, executor) -> list[str]:
    """把 catalog 中全部 Skill 注册为 /<名字> 命令；返回本次成功注册的名字列表。

    再调用先清旧（remove_skill_commands），保证 reload 后无残留。
    """
    remove_skill_commands(reg)
    registered: list[str] = []
    for skill in catalog.list():
        cmd = CommandDef(
            name=skill.name,
            kind=CommandKind.UI,  # 触发回合（inline）或后台 fork 都需 idle 状态机门
            description=f"{skill.meta.description} [skill]",
            handler=functools.partial(_handle_skill_command, name=skill.name),
        )
        try:
            reg.register(cmd)
        except RuntimeError:
            # F2.5：与内置命令冲突 → 跳过注册并记日志（内置优先）
            logger.warning(
                "skill %s conflicts with builtin command, /%s registration skipped",
                skill.name,
                skill.name,
            )
            continue
        _REGISTERED_SKILL_NAMES.add(skill.name)
        registered.append(skill.name)
    return registered


def remove_skill_commands(reg: CommandRegistry) -> int:
    """清掉所有 `[skill]` 标注命令（reload / 卸载时同步，T19）。"""
    removed = reg.remove_by(lambda c: c.description.endswith("[skill]"))
    _REGISTERED_SKILL_NAMES.clear()
    return removed
