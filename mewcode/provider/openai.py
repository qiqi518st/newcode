"""OpenAI Provider：SSE 流式、StreamEvent"""

from openai import AsyncOpenAI, APIError as OpenAIAPIError

from .base import Message, StreamEvent, Provider
from ..config.schema import ProviderConfig
from ..utils.error import ProviderError


class OpenAIProvider:
    """OpenAI 协议适配器，满足 Provider 协议"""

    def __init__(self, cfg: ProviderConfig) -> None:
        kwargs: dict = {"api_key": cfg.api_key}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        self._client = AsyncOpenAI(**kwargs)
        self._name = cfg.name
        self._model = cfg.model

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    async def stream(self, msgs: list[Message]) -> "AsyncIterator[StreamEvent]":
        """发起 OpenAI 流式对话请求"""
        api_messages: list[dict] = []
        for msg in msgs:
            # OpenAI 支持 system 角色
            api_messages.append({"role": msg.role, "content": msg.content})

        try:
            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=api_messages,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield StreamEvent(text=delta.content)
            yield StreamEvent(done=True)
        except OpenAIAPIError as e:
            yield StreamEvent(err=ProviderError(f"OpenAI API 错误: {e}"))
        except Exception as e:
            yield StreamEvent(err=ProviderError(f"OpenAI 请求失败: {e}"))