"""Anthropic Provider：流式 SSE、tool_use 解析、工具结果回灌"""

from anthropic import AsyncAnthropic, APIError as AnthropicAPIError

from .base import Message, StreamEvent, ToolCall, ToolDefinition, TokenUsage
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

    async def stream(
        self,
        msgs: list[Message],
        tools: list[ToolDefinition] | None = None,
        system_suffix: str = "",
    ) -> "AsyncIterator[StreamEvent]":
        """发起 Anthropic 流式对话请求，支持工具调用"""
        system_prompt = ""
        api_messages: list[dict] = []
        for msg in msgs:
            if msg.role == "system":
                system_prompt = msg.content
            elif msg.role == "tool":
                # Anthropic tool_result 格式
                api_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_use_id or "",
                        "content": msg.content,
                        "is_error": False,
                    }],
                })
            elif msg.role == "assistant" and msg.tool_calls:
                # Anthropic assistant tool_use 声明格式
                api_messages.append({
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
                })
            else:
                api_messages.append({"role": msg.role, "content": msg.content})

        kwargs: dict = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": 4096,
        }
        if system_prompt:
            kwargs["system"] = system_prompt + system_suffix
        if self._thinking:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 1024}
        if tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]

        # 流式消费状态
        _tool_name: str | None = None
        _tool_use_id: str | None = None
        _partial_json: str = ""

        try:
            async with self._client.messages.stream(**kwargs) as stream:
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
                                arguments = json.loads(_partial_json) if _partial_json else {}
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

                    elif event_type == "message_stop":
                        # 提取 token 用量
                        usage = None
                        if hasattr(event, "message") and hasattr(event.message, "usage"):
                            u = event.message.usage
                            usage = TokenUsage(
                                input_tokens=getattr(u, "input_tokens", 0),
                                output_tokens=getattr(u, "output_tokens", 0),
                            )
                        yield StreamEvent(done=True, usage=usage)

        except AnthropicAPIError as e:
            yield StreamEvent(err=ProviderError(f"Anthropic API 错误: {e}"))
        except Exception as e:
            yield StreamEvent(err=ProviderError(f"Anthropic 请求失败: {e}"))