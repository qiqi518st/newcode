"""SubAgent 运行期错误（ch13 F5.2）：MaxTurnsReached。

子 Agent 触达 maxTurns 时抛此错误——携带最后一条 assistant 文本与用量，
供 TaskManager 转 status=failed 并保留结果（spec F5.2/F7.5）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..provider.base import TokenUsage


class MaxTurnsReached(Exception):
    """子 Agent 触达 max_turns（spec F5.2）：正常文本仍可读，但视为失败终止。"""

    def __init__(
        self, text: str, usage: TokenUsage | None = None, tool_count: int = 0
    ) -> None:
        self.text = text  # 最后一条 assistant 文本（保留给任务 result）
        self.usage = usage
        self.tool_count = tool_count
        super().__init__("subagent reached max turns")
