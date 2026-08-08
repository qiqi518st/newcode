"""OpenAI Provider：SSE 流式、function_call 解析、工具结果回灌"""

from openai import AsyncOpenAI, APIError as OpenAIAPIError

from .base import Message, StreamEvent, ToolCall, ToolDefinition
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

    async def stream(
        self,
        msgs: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> "AsyncIterator[StreamEvent]":
        """发起 OpenAI 流式对话请求，支持工具调用"""
        api_messages: list[dict] = []
        for msg in msgs:
            if msg.role == "tool":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id or "",
                    "name": msg.name or "",
                    "content": msg.content,
                })
            elif msg.role == "assistant" and msg.tool_calls:
                # OpenAI assistant tool_calls 声明格式
                import json
                api_messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })
            else:
                api_messages.append({"role": msg.role, "content": msg.content})

        kwargs: dict = {
            "model": self._model,
            "messages": api_messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        # tool_calls 分片拼接状态：index -> {"id": ..., "name": ..., "arguments": ...}
        _tool_call_buffers: dict[int, dict] = {}

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue

                # 优先处理 tool_calls（出现时 content 通常为空）
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in _tool_call_buffers:
                            _tool_call_buffers[idx] = {"id": "", "name": "", "arguments": ""}
                        buf = _tool_call_buffers[idx]
                        if tc.id:
                            buf["id"] = tc.id
                        if tc.function and tc.function.name:
                            buf["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            buf["arguments"] += tc.function.arguments
                    continue

                # 检查是否有已完成的 tool_call 需要吐出
                completed = []
                for idx, buf in list(_tool_call_buffers.items()):
                    if buf["id"] and buf["name"] and buf["arguments"]:
                        import json
                        try:
                            arguments = json.loads(buf["arguments"])
                        except json.JSONDecodeError:
                            arguments = {}
                        completed.append((idx, buf["id"], buf["name"], arguments))
                if completed:
                    # OpenAI 通常一次只有一个 tool_call
                    for _idx, tc_id, tc_name, args in completed:
                        yield StreamEvent(
                            tool_call=ToolCall(
                                tool_name=tc_name,
                                arguments=args,
                                tool_call_id=tc_id,
                            )
                        )
                    _tool_call_buffers.clear()

                if delta.content:
                    yield StreamEvent(text=delta.content)

            yield StreamEvent(done=True)
        except OpenAIAPIError as e:
            yield StreamEvent(err=ProviderError(f"OpenAI API 错误: {e}"))
        except Exception as e:
            yield StreamEvent(err=ProviderError(f"OpenAI 请求失败: {e}"))