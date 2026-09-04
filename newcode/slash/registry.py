"""命令注册中心（F1）：CommandKind / CommandDef / CommandRegistry。

- RLock 读写锁保护注册/查找/列举/补全并发安全（为 Skill 动态注册预留，F1.4/N9）。
- register 在启动期做名字/别名冲突检测，冲突抛 RuntimeError 且消息含冲突名（F1.3/N4）。
- 名字与别名大小写不敏感（F2.2/N10）；list 按 name 字典序且返回新拷贝（防外部改动）。
- 命令实现只依赖 UIController，不 import prompt_toolkit / rich（F6.3）。
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import CommandContext


class CommandKind(Enum):
    """命令执行类型（F3.1）：纯本地 / 影响界面 / 提示词"""

    LOCAL = "local"
    UI = "ui"
    PROMPT = "prompt"


# handler 统一签名：async def handler(ctx: CommandContext, args: str) -> None（F5.2）
Handler = Callable[["CommandContext", str], Awaitable[None]]


@dataclass(frozen=True)
class CommandDef:
    """单条命令的元数据（F1.1/F1.2）"""

    name: str  # 命令名（不含 /，小写）
    handler: Handler  # 处理函数
    description: str = ""  # 一句描述（/help、补全菜单共用）
    kind: CommandKind = CommandKind.LOCAL  # LOCAL / UI / PROMPT
    aliases: tuple[str, ...] = ()  # 别名集合
    usage: str = ""  # 用法示例（含参数形式）；空表示不带参数
    arg_prompt: str = ""  # 参数格式提示（Tab 补全补充，F9.7）
    hidden: bool = False  # 隐藏命令：不进 /help 与补全，dispatcher 仍命中（F10）


class CommandRegistry:
    """注册中心：register / get / list / complete（读写锁，名字/别名大小写不敏感）"""

    def __init__(self) -> None:
        self._commands: dict[str, CommandDef] = {}
        self._lock = threading.RLock()

    def register(self, cmd: CommandDef) -> None:
        """注册一条命令；name 或任一 alias 与既有键冲突时抛 RuntimeError（含冲突键）。"""
        with self._lock:
            keys = (cmd.name,) + tuple(cmd.aliases)
            for key in keys:
                lowered = key.lower()
                if not lowered:
                    raise ValueError(f"command name/alias must be non-empty: {key!r}")
                if lowered in self._commands:
                    raise RuntimeError(f"command name/alias conflict: {key}")
            for key in keys:
                self._commands[key.lower()] = cmd

    def get(self, name: str) -> CommandDef | None:
        """按名字或别名查找，大小写不敏感。"""
        with self._lock:
            return self._commands.get(name.lower())

    def unregister(self, name: str) -> bool:
        """按名字或别名移除一条命令（含别名键，ch11 /skill unload 与 reload 用）。

        返回是否实际移除了命令（该命令的所有键全部删除）。
        """
        with self._lock:
            cmd = self._commands.get(name.lower())
            if cmd is None:
                return False
            keys = (cmd.name,) + tuple(cmd.aliases)
            for key in keys:
                self._commands.pop(key.lower(), None)
            return True

    def remove_by(self, predicate) -> int:
        """按谓词批量移除命令（供 remove_skill_commands 同步 /名字 注册，ch11）。

        predicate 接收 CommandDef，返回 True 表示移除；返回移除条数。
        """
        with self._lock:
            victims = [
                c for c in dict.fromkeys(self._commands.values()) if predicate(c)
            ]
            count = 0
            for cmd in victims:
                keys = (cmd.name,) + tuple(cmd.aliases)
                removed = any(
                    self._commands.pop(key.lower(), None) is not None for key in keys
                )
                count += 1 if removed else 0
            return count

    def list(self, include_hidden: bool = False) -> list[CommandDef]:
        """按 name 字典序返回命令列表；默认排除 hidden。

        返回排序后的新 list 拷贝，不暴露内部 dict（防外部改动影响注册中心）。
        因别名与名字指向同一 CommandDef 对象，先按出现顺序去重再排序。
        """
        with self._lock:
            unique = list(dict.fromkeys(self._commands.values()))
            result = [c for c in unique if include_hidden or not c.hidden]
            result.sort(key=lambda c: c.name)
            return result

    def complete(self, prefix: str) -> list[CommandDef]:
        """按命令名前缀过滤（仅 name，不匹配别名/描述，F9.2）；排除 hidden；字典序。"""
        p = prefix.lstrip("/").lower()
        return [c for c in self.list() if c.name.startswith(p)]
