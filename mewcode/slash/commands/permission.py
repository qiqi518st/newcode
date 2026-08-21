"""/permission 家族（F8.11/F8.17-F8.19）：只读模式 / 列规则 / 加规则 / 重置规则。"""

from __future__ import annotations

from ..context import CommandContext
from ..registry import CommandDef, CommandKind

_USAGE_ADD = (
    "/permission_add <规则> <效果>"  # 规则形如 "Bash(git *)"，效果 ∈ allow/deny
)


async def handle_permission(ctx: CommandContext, _args: str) -> None:
    """F8.11：只读输出当前权限模式名称（与 /status 相同字符串形式）。"""
    ctx.ui.show_message(ctx.ui.get_permission_mode())


async def handle_permission_rules(ctx: CommandContext, _args: str) -> None:
    """F8.17：列出当前生效的权限规则（local/project/user 三层）。"""
    checker = ctx.permission
    if checker is None:
        ctx.ui.show_message("权限检查器未接线", style="yellow")
        return
    layers = checker._layers  # 内部结构：本模块属同包使用方，仅读取不改写
    rows: list[str] = []
    for level in ("local", "project", "user"):
        layer = getattr(layers, level)
        for rule in layer.allow:
            rows.append(f"{level} allow {rule.tool_name}({rule.pattern})")
        for rule in layer.deny:
            rows.append(f"{level} deny {rule.tool_name}({rule.pattern})")
    if not rows:
        ctx.ui.show_message("（无生效规则）", style="dim")
        return
    ctx.ui.show_message("\n".join(rows))


async def handle_permission_add(ctx: CommandContext, args: str) -> None:
    """F8.18：新增一条本地权限规则（立即生效）。"""
    checker = ctx.permission
    if checker is None:
        ctx.ui.show_message("权限检查器未接线", style="yellow")
        return
    # 规则串可能含空格（如 "Bash(git *)"）→ 用 rsplit 把最后一个 token 当作效果
    parts = args.rsplit(None, 1)
    if len(parts) != 2:
        ctx.ui.show_message(f"用法: {_USAGE_ADD}", style="yellow")
        return
    pattern, effect = parts[0], parts[1]
    if effect not in ("allow", "deny"):
        ctx.ui.show_message(
            f"效果必须是 allow 或 deny（收到: {effect}）", style="yellow"
        )
        return
    try:
        checker.add_rule(pattern, effect)
    except (ValueError, OSError) as exc:
        ctx.ui.show_message(f"添加规则失败: {exc}", style="red")
        return
    ctx.ui.show_message(f"已添加规则: {pattern} → {effect}", style="green")


async def handle_permission_reset(ctx: CommandContext, _args: str) -> None:
    """F8.19：清空本地规则，返回删除条数。"""
    checker = ctx.permission
    if checker is None:
        ctx.ui.show_message("权限检查器未接线", style="yellow")
        return
    removed = checker.reset_rules()
    ctx.ui.show_message(
        f"已清空本地权限规则（{removed} 条）" if removed else "（本地无权限规则）",
        style="green",
    )


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="permission",
            kind=CommandKind.LOCAL,
            description="显示当前权限模式",
            handler=handle_permission,
        ),
        CommandDef(
            name="permission_rules",
            kind=CommandKind.LOCAL,
            description="列出当前生效的权限规则",
            handler=handle_permission_rules,
        ),
        CommandDef(
            name="permission_add",
            kind=CommandKind.LOCAL,
            description="新增一条权限规则",
            handler=handle_permission_add,
            usage=_USAGE_ADD,
            arg_prompt="<规则> <效果>",
        ),
        CommandDef(
            name="permission_reset",
            kind=CommandKind.LOCAL,
            description="清空本地权限规则",
            handler=handle_permission_reset,
        ),
    ]
