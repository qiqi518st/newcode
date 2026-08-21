"""/review（F8.13）：注入固定"代码审查请求"文本并触发回合（不读 git diff）。"""

from __future__ import annotations

from ..context import CommandContext
from ..registry import CommandDef, CommandKind

# 固定"代码审查请求"文本（F8.13：含审查关键字，但一律不读 diff / 不收集外部上下文）
REVIEW_DIRECTIVE = (
    "请审查当前上下文中的代码变更/已读取的文件，"
    "指出潜在 bug、可读性问题和可简化处。"
)


async def handle_review(ctx: CommandContext, _args: str) -> None:
    """F8.13：注入审查请求并立即触发一轮 normal 回合；注入消息与真实用户消息同持久化路径（F3.4）。"""
    await ctx.ui.send_user_message(REVIEW_DIRECTIVE)


def build() -> list[CommandDef]:
    return [
        CommandDef(
            name="review",
            kind=CommandKind.PROMPT,
            description="审查当前上下文中的代码变更",
            handler=handle_review,
        )
    ]