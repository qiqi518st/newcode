"""/help：按字典序列出全部已注册命令（F8.8/AC1）。"""

from __future__ import annotations

from ..context import CommandContext
from ..registry import CommandDef, CommandKind


async def _handler(ctx: CommandContext, _args: str) -> None:
    cmds = ctx.registry.list()
    if not cmds:
        ctx.ui.show_message("（暂无可用命令）", style="dim")
        return
    # 两列对齐：key 列宽 = 最长 name 长度（T7 实现细节）
    width = max(len(c.name) for c in cmds)
    lines = [f"/{c.name.ljust(width)}  {c.description}" for c in cmds]
    ctx.ui.show_message("\n".join(lines))


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="help",
            kind=CommandKind.LOCAL,
            description="显示所有可用命令",
            handler=_handler,
        )
    ]