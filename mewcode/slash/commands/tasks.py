"""/tasks 命令族（ch13 F8.3）：列出 / 详情 / 终止 / 续派后台子 Agent 任务。

与 Task 工具组共用同一 TaskManager 底层；user 侧管理入口（主 Agent 走工具）。
"""

from __future__ import annotations

import time

from ..context import CommandContext
from ..registry import CommandDef, CommandKind


async def _handler(ctx: CommandContext, args: str) -> None:
    mgr = getattr(ctx, "task_manager", None)
    if mgr is None:
        ctx.ui.show_message("后台任务管理未启用", style="yellow")
        return
    parts = args.split(None, 1)
    sub = parts[0].strip().lower() if parts and parts[0].strip() else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub in ("", "list"):
        tasks = mgr.list()
        if not tasks:
            ctx.ui.show_message("No background tasks.")
            return
        rows = []
        for t in tasks:
            dur = (t.end_time or time.monotonic()) - t.start_time
            label = t.role or t.name or t.id
            rows.append(
                f"{t.id}  {t.status.name.lower()}  {label}  {dur:.0f}s  "
                f"in:{t.usage.input_tokens} out:{t.usage.output_tokens}"
            )
        ctx.ui.show_message("\n".join(rows))
        return

    if sub == "show":
        if not rest:
            ctx.ui.show_message("用法: /tasks show <task_id>", style="yellow")
            return
        task = mgr.get(rest)
        if task is None:
            ctx.ui.show_message(f"任务不存在: {rest}", style="yellow")
            return
        err = str(task.err) if task.err else ""
        ctx.ui.show_message(
            "\n".join(
                [
                    f"id: {task.id}",
                    f"name: {task.name or '-'}",
                    f"role: {task.role or '-'}",
                    f"status: {task.status.name.lower()}",
                    f"round: {task.round}",
                    f"tool_count: {task.tool_count}",
                    f"usage in/out: {task.total_usage.input_tokens}/{task.total_usage.output_tokens}",
                    f"start: {task.start_time:.1f}  end: {task.end_time or 0:.1f}",
                    f"result: {task.result or '-'}",
                    *([f"error: {err}"] if err else []),
                ]
            )
        )
        return

    if sub == "kill":
        if not rest:
            ctx.ui.show_message("用法: /tasks kill <task_id>", style="yellow")
            return
        if not mgr.stop(rest):
            ctx.ui.show_message(f"任务不存在: {rest}", style="yellow")
            return
        ctx.ui.show_message(f"已请求终止 {rest}", style="green")
        return

    if sub == "send":
        parts2 = rest.split(None, 1)
        if len(parts2) != 2:
            ctx.ui.show_message("用法: /tasks send <task_id|name> <message>", style="yellow")
            return
        target, message = parts2[0], parts2[1]
        try:
            task_id = mgr.continue_agent(target, message)
        except Exception as exc:  # noqa: BLE001 —— 续派错误转文案
            ctx.ui.show_message(f"续派失败: {exc}", style="red")
            return
        ctx.ui.show_message(f"已续派 {task_id}", style="green")
        return

    ctx.ui.show_message(f"未知子命令: {sub}（/tasks [show|kill|send] ...）", style="yellow")


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="tasks",
            kind=CommandKind.LOCAL,
            description="后台子 Agent 任务：列出 / show / kill / send",
            usage="/tasks | /tasks show <id> | /tasks kill <id> | /tasks send <id|name> <message>",
            handler=_handler,
        )
    ]
