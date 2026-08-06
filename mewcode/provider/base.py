"""LLM 协议无关类型：Provider Protocol、StreamEvent、Message"""

from dataclasses import dataclass
from typing import Literal, Protocol, AsyncIterator

from ..config.schema import ProviderConfig
from ..utils.error import ConfigError


@dataclass
class Message:
    """单条对话消息"""
    role: Literal["user", "assistant", "system"]
    content: str


@dataclass
class StreamEvent:
    """流式事件：text / done / err 三者互斥"""
    text: str = ""                             # 文本增量
    done: bool = False                         # 本轮正常结束
    err: Exception | None = None               # 出错（与 done 互斥）


class Provider(Protocol):
    """LLM Provider 协议，所有后端通过此接口统一调用"""

    @property
    def name(self) -> str:
        """Provider 名称，用于状态栏左"""
        ...

    @property
    def model(self) -> str:
        """模型名，用于状态栏右"""
        ...

    def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]:
        """发起一轮流式对话；内部注入 system prompt 与 thinking 配置；
        思考增量内部丢弃；以 async generator 吐出 StreamEvent；
        调用方 cancel() 该 task 即终止。
        """
        ...


def new_provider(cfg: ProviderConfig) -> Provider:
    """按 protocol 构造适配器"""
    if cfg.protocol == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(cfg)
    elif cfg.protocol == "openai":
        from .openai import OpenAIProvider
        return OpenAIProvider(cfg)
    else:
        raise ConfigError(f"Unknown protocol: {cfg.protocol}")