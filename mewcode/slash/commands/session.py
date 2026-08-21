"""/session 家族（F8.12/F8.20-F8.22）：只读当前会话 / 列历史 / 恢复 / 新建。"""

from __future__ import annotations

from ..context import CommandContext
from ..registry import CommandDef, CommandKind

_USAGE_RESUME = "/session_resume <id>"  # id 形如 20260820-120000-abcd


async def handle_session(ctx: CommandContext, _args: str) -> None:
    """F8.12：只读输出当前会话的会话存档路径与 session 标识。"""
    runtime = ctx.session_runtime
    if runtime is None or runtime.context is None:
        ctx.ui.show_message("当前未启用会话持久化", style="yellow")
        return
    sid = runtime.context.session_id
    path = (
        str(runtime.writer.path)
        if runtime.writer is not None
        else str(runtime.context.conversation_path or "")
    )
    ctx.ui.show_message(f"Session: {sid}\nPath: {path}")


async def handle_session_list(ctx: CommandContext, _args: str) -> None:
    """F8.20：列出可恢复的会话。"""
    archive = ctx.session_archive
    if archive is None:
        ctx.ui.show_message("当前未启用会话管理", style="yellow")
        return
    sessions = archive.list()
    if not sessions:
        ctx.ui.show_message("（无历史会话）", style="dim")
        return
    rows = [
        f"{s.session_id}  {s.title or '(无标题)'}  {s.model or ''}  {s.message_count} msgs"
        for s in sessions
    ]
    ctx.ui.show_message("\n".join(rows))


async def handle_session_resume(ctx: CommandContext, args: str) -> None:
    """F8.21：恢复指定会话（id 校验 + 友好错误）。"""
    sid = args.strip().split()[0] if args.strip() else ""
    if not sid:
        ctx.ui.show_message(f"用法: {_USAGE_RESUME}", style="yellow")
        return
    archive = ctx.session_archive
    if archive is not None:
        known = {s.session_id for s in archive.list()}
        if sid not in known:
            ctx.ui.show_message(f"未找到会话: {sid}", style="yellow")
            return
    try:
        await ctx.ui.resume_session(sid)
    except Exception as exc:  # noqa: BLE001 — 恢复失败对用户可见，不崩 TUI
        ctx.ui.show_message(f"恢复会话失败: {exc}", style="red")
        return
    ctx.ui.show_message(f"已恢复会话 {sid}", style="green")


async def handle_session_new(ctx: CommandContext, _args: str) -> None:
    """F8.22：新建会话。"""
    try:
        await ctx.ui.new_session()
    except Exception as exc:  # noqa: BLE001
        ctx.ui.show_message(f"新建会话失败: {exc}", style="red")
        return
    ctx.ui.show_message("已创建新会话", style="green")


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="session",
            kind=CommandKind.LOCAL,
            description="显示当前会话路径与标识",
            handler=handle_session,
        ),
        CommandDef(
            name="session_list",
            kind=CommandKind.LOCAL,
            description="列出可恢复的会话",
            handler=handle_session_list,
        ),
        CommandDef(
            name="session_resume",
            kind=CommandKind.UI,
            description="恢复指定会话",
            handler=handle_session_resume,
            usage=_USAGE_RESUME,
            arg_prompt="<会话id>",
        ),
        CommandDef(
            name="session_new",
            kind=CommandKind.UI,
            description="新建会话",
            handler=handle_session_new,
        ),
    ]
