"""/status：六项综合状态（F8.9/AC4，顺序固定按列对齐 N6）。"""

from __future__ import annotations

from ..context import CommandContext
from ..registry import CommandDef, CommandKind

# 六项 key（顺序固定）；key 列宽取最长者，对 value 列实现对齐（T7 实现细节）
_KEYS = ["Mode", "Tokens", "Tools", "Memories", "Model", "Directory"]


async def _handler(ctx: CommandContext, _args: str) -> None:
    ui = ctx.ui
    in_t, out_t = ui.query_token_usage()
    width = max(len(k) for k in _KEYS)
    lines = [
        "MewCode Status",
        "",
        f"{'Mode'.ljust(width)}:  {ui.get_permission_mode()}",
        f"{'Tokens'.ljust(width)}:  {in_t} in / {out_t} out",
        f"{'Tools'.ljust(width)}:  {ui.query_tool_count()} enabled",
        f"{'Memories'.ljust(width)}:  {len(ui.query_memory_files())} files",
        f"{'Model'.ljust(width)}:  {ui.get_model_name()}",
        f"{'Directory'.ljust(width)}:  {ui.get_cwd()}",
    ]
    ui.show_message("\n".join(lines))


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="status",
            kind=CommandKind.LOCAL,
            description="显示当前会话状态（模式/token/工具/记忆/模型/目录）",
            handler=_handler,
        )
    ]
