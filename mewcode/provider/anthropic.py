"""Anthropic Provider：通过 Anthropic SDK 调用 LLM"""

import httpx
from anthropic import AsyncAnthropic, APIError as AnthropicAPIError

from .base import Message, StreamEvent
from ..config.schema import ProviderConfig
from ..utils.error import ProviderError


class AnthropicProvider:
    """Anthropic 协议适配器，满足 Provider 协议"""

    def __init__(self, cfg: ProviderConfig) -> None:
        # auth_token 优先（Bearer 认证），否则用 api_key（x-api-key 认证）
        client_kwargs: dict = {}
        if cfg.auth_token:
            client_kwargs["auth_token"] = cfg.auth_token
        else:
            client_kwargs["api_key"] = cfg.api_key
        if cfg.base_url:
            client_kwargs["base_url"] = cfg.base_url
        self._client = AsyncAnthropic(**client_kwargs)
        self._name = cfg.name
        self._model = cfg.model
        self._thinking = cfg.thinking

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    async def stream(self, msgs: list[Message]) -> "AsyncIterator[StreamEvent]":
        """发起对话请求，流式输出回复"""
        system_prompt = ""
        api_messages: list[dict] = []
        for msg in msgs:
            if msg.role == "system":
                system_prompt = msg.content
            elif msg.role in ("user", "assistant"):
                api_messages.append({"role": msg.role, "content": msg.content})

        kwargs: dict = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": 4096,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if self._thinking:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 1024}

        try:
            msg = await self._client.messages.create(**kwargs)
            # 提取 text 块（过滤 thinking 块）
            full_text = "".join(
                block.text for block in msg.content if block.type == "text"
            )
            # 按合理大小分块 yield 模拟流式
            chunk_size = max(4, len(full_text) // 10)
            pos = 0
            while pos < len(full_text):
                end = min(pos + chunk_size, len(full_text))
                # 确保不在多字节字符中间截断
                yield StreamEvent(text=full_text[pos:end])
                pos = end
            yield StreamEvent(done=True)
        except AnthropicAPIError as e:
            yield StreamEvent(err=ProviderError(f"Anthropic API 错误: {e}"))
        except Exception as e:
            yield StreamEvent(err=ProviderError(f"Anthropic 请求失败: {e}"))