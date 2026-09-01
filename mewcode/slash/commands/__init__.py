"""内置命令装配（T6）：register_all(registry) 一次性注册全部命令。

每个命令模块导出 build() -> list[CommandDef]；handler 在调用时经 CommandContext
取用资源（ctx.ui / ctx.plan_manager / ...），build 无需依赖参数。

ch11：/review 硬编码命令移除（F6.4），由 review Skill（fork）自动注册接管；
/skill 管理命令加入；skill_register 提供 Skill 动态 /名字 注册（装配在 main.py）。
"""

from __future__ import annotations

from ..registry import CommandRegistry
from . import (
    clear,
    compact,
    do,
    hooks,
    legacy,
    memory,
    permission,
    plan,
    session,
    skill,
    status,
    tasks,
)
from . import help as help_cmd

# 遍历顺序即注册顺序（无依赖；list() 会按 name 字典序输出）
COMMAND_MODULES = [
    help_cmd,
    status,
    memory,
    permission,
    session,
    plan,
    do,
    clear,
    compact,
    skill,
    hooks,
    tasks,
    legacy,
]


def register_all(registry: CommandRegistry) -> None:
    """遍历各模块 build() 收集 CommandDef 并注册；冲突抛 RuntimeError（N4/F1.3）。"""
    for module in COMMAND_MODULES:
        for cmd in module.build():
            registry.register(cmd)
