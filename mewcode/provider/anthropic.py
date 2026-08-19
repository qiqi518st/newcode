"""Anthropic Provider：流式 SSE、tool_use 解析、工具结果回灌、缓存标记与命中解析"""

from collections.abc import AsyncIterator

from anthropic import APIError as AnthropicAPIError
from anthropic import AsyncAnthropic

from ..config.schema import ProviderConfig
from ..llm import PromptTooLongError
from ..prompt.assembler import PromptPayload
from ..utils.error import ProviderError
from .base import StreamEvent, TokenUsage, ToolCall


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

    async def stream(
        self,
        payload: PromptPayload,
    ) -> "AsyncIterator[StreamEvent]":
        """发起 Anthropic 流式对话请求。

        首条 user 消息为「段1 稳定系统提示（cache_control 缓存断点）+ 段2 环境信息（不缓存）」，
        历史与轮次级 reminders 依次翻译；tools 通道整体打缓存断点。
        """
        api_messages: list[dict] = []

        # 首条 user 消息：稳定系统提示（缓存断点）+ 环境信息（断点之后不缓存）
        first_content: list[dict] = []
        if payload.stable_prompt:
            first_content.append(
                {
                    "type": "text",
                    "text": payload.stable_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        if payload.env_segment:
            first_content.append({"type": "text", "text": payload.env_segment})
        if first_content:
            api_messages.append({"role": "user", "content": first_content})

        # 会话历史
        for msg in payload.messages:
            if msg.role == "tool":
                # Anthropic tool_result 格式
                api_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_use_id or "",
                                "content": msg.content,
                                "is_error": False,
                            }
                        ],
                    }
                )
            elif msg.role == "assistant" and msg.tool_calls:
                # Anthropic assistant tool_use 声明格式
                api_messages.append(
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": tc["id"],
                                "name": tc["name"],
                                "input": tc["arguments"],
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )
            else:
                api_messages.append({"role": msg.role, "content": msg.content})

        # 轮次级 system-reminder（role=user，<system-reminder> 标签，瞬时不持久）
        for r in payload.reminders:
            api_messages.append({"role": "user", "content": r.content})

        kwargs: dict = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": payload.max_output_tokens or 4096,
        }
        if self._thinking:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 1024}
        if payload.tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                    "cache_control": {"type": "ephemeral"},
                }
                for t in payload.tools
            ]

        # 流式消费状态
        _tool_name: str | None = None
        _tool_use_id: str | None = None
        _partial_json: str = ""
        # usage 从 message_start 初始值 + message_delta 累计值合并
        _usage: TokenUsage | None = None

        # 注意：不用 SDK 的 messages.stream()（内部快照累积会因
        # message_start 缺 content 字段而崩溃，如经 CC Switch 中转的
        # deepseek 响应），改用 create(stream=True) 直接消费原始事件流。
        try:
            stream = await self._client.messages.create(stream=True, **kwargs)
            try:
                async for event in stream:
                    event_type = event.type

                    if event_type == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            _tool_name = block.name
                            _tool_use_id = block.id
                            _partial_json = ""
                        elif block.type == "text":
                            # 文本块开始，无增量
                            pass

                    elif event_type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield StreamEvent(text=delta.text)
                        elif delta.type == "input_json_delta":
                            _partial_json += delta.partial_json

                    elif event_type == "content_block_stop":
                        if _tool_name is not None:
                            # tool_use 块结束，解析 JSON
                            import json

                            try:
                                arguments = (
                                    json.loads(_partial_json) if _partial_json else {}
                                )
                            except json.JSONDecodeError:
                                arguments = {}
                            yield StreamEvent(
                                tool_call=ToolCall(
                                    tool_name=_tool_name,
                                    arguments=arguments,
                                    tool_use_id=_tool_use_id or "",
                                )
                            )
                            _tool_name = None
                            _tool_use_id = None
                            _partial_json = ""

                    elif event_type == "message_start":
                        # 初始 usage（input 计数），含缓存创建/读取字段
                        u = getattr(event.message, "usage", None)
                        if u is not None:
                            _usage = _to_usage(u)

                    elif event_type == "message_delta":
                        # 累计 usage（覆盖，message_delta 携带最终计数）
                        u = getattr(event, "usage", None)
                        if u is not None:
                            _usage = _to_usage(u)

                    elif event_type == "message_stop":
                        yield StreamEvent(done=True, usage=_usage)
            finally:
                await stream.close()

        except AnthropicAPIError as e:
            yield StreamEvent(err=_wrap_anthropic_error(e))
        except Exception as e:  # noqa: BLE001 — 流式消费中任何异常都应包装为 ProviderError，不崩溃
            yield StreamEvent(err=ProviderError(f"Anthropic 请求失败: {e}"))


def _to_usage(u: object) -> TokenUsage:
    """解析 Anthropic usage → TokenUsage；缓存字段缺失按 0（N1 健壮解析）"""
    return TokenUsage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
    )


def _wrap_anthropic_error(e: AnthropicAPIError) -> PromptTooLongError | ProviderError:
    """Anthropic 400 错误中识别 PTL 关键词，wrap 为 PromptTooLongError 哨兵（保留 __cause__）。"""
    if getattr(e, "status_code", None) == 400:
        msg = str(e)
        if "prompt is too long" in msg or "context length" in msg:
            wrapped = PromptTooLongError(str(e))
            wrapped.__cause__ = e
            return wrapped
    return ProviderError(f"Anthropic API 错误: {e}")
