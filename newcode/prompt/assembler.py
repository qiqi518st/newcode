"""Prompt 组装管线：三通道分发 + 缓存通道规划（spec F2 / N8）"""

import logging
from dataclasses import dataclass, field

from ..provider.base import Message, ToolDefinition

logger = logging.getLogger(__name__)


@dataclass
class PromptPayload:
    """协议无关的请求负载（三通道分发结果）"""

    stable_prompt: str  # 段1：稳定系统提示（跨轮逐字节一致，可缓存）
    env_segment: str  # 段2：环境信息（断点之后，不缓存）
    messages: list[Message] = field(default_factory=list)  # 会话历史（不缓存）
    reminders: list[Message] = field(
        default_factory=list
    )  # 轮次级 system-reminder（瞬时不持久）
    tools: list[ToolDefinition] | None = None  # 工具定义（可缓存）
    max_output_tokens: int | None = (
        None  # 输出 token 上限覆盖（摘要等独立请求用；None=provider 默认）
    )
    trace_context: dict[str, object] | None = None


class PayloadAssembler:
    """组装管线：分类路由 + 稳定前缀跨轮逐字节一致校验

    稳定不变内容（stable_prompt + tools）归入可缓存通道，变化内容
    （env_segment + messages + reminders）归入不缓存通道。
    """

    def __init__(self) -> None:
        self._last_stable: str | None = None

    def assemble(
        self,
        stable_prompt: str,
        env_segment: str,
        history: list[Message],
        reminders: list[Message],
        tools: list[ToolDefinition] | None,
    ) -> PromptPayload:
        """分类路由各通道；stable_prompt 跨轮变化时日志告警（缓存会失效）"""
        if self._last_stable is not None and stable_prompt != self._last_stable:
            logger.warning(
                "稳定系统提示跨轮变化（会破坏缓存命中）: 前 %d 字符 → 新 %d 字符",
                len(self._last_stable),
                len(stable_prompt),
            )
        self._last_stable = stable_prompt
        return PromptPayload(
            stable_prompt=stable_prompt,
            env_segment=env_segment,
            messages=list(history),
            reminders=list(reminders),
            tools=tools,
        )
