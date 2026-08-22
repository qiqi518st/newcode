"""/hooks（F10）：列出已加载的 Hook 规则，按 event 分组。"""

from __future__ import annotations

from ..context import CommandContext
from ..registry import CommandDef, CommandKind


async def _handler(ctx: CommandContext, _args: str) -> None:
    hooks = getattr(ctx, "hooks", None)
    if hooks is None:
        ctx.ui.show_message("Hook 系统未启用", style="yellow")
        return
    rules = hooks.rules
    if not rules:
        ctx.ui.show_message("No hooks loaded.")
        return
    # 按 event 分组（保持声明顺序）
    groups: dict[str, list] = {}
    for r in rules:
        groups.setdefault(r.event.value, []).append(r)
    lines: list[str] = []
    for event_name, group in groups.items():
        lines.append(f"[{event_name}]")
        for r in group:
            flags = []
            if r.once:
                flags.append("[once]")
            if r.asyncio_mode:
                flags.append("[async]")
            flag_part = (" " + " ".join(flags)) if flags else ""
            lines.append(f"  {r.name}  {r.event.value}  {r.action.type.value}{flag_part}")
    lines.append(f"Loaded from: {', '.join(hooks.sources)}")
    ctx.ui.show_message("\n".join(lines))


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="hooks",
            kind=CommandKind.LOCAL,
            description="列出已加载的 Hook 规则",
            handler=_handler,
        )
    ]
