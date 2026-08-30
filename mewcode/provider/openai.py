"""OpenAI Provider：SSE 流式、function_call 解析、工具结果回灌、cached_tokens 解析"""

from collections.abc import AsyncIterator

from openai import APIError as OpenAIAPIError
from openai import AsyncOpenAI

from ..config.schema import ProviderConfig
from ..llm import PromptTooLongError
from ..monitor.protocol import write_request_record
from ..prompt.assembler import PromptPayload
from ..utils.error import ProviderError
from .base import StreamEvent, TokenUsage, ToolCall, api_model


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
        payload: PromptPayload,
    ) -> "AsyncIterator[StreamEvent]":
        """发起 OpenAI 流式对话请求。

        段1 稳定系统提示与段2 环境信息各为一条 user 消息（段1 在前，保稳定前缀）
        ——OpenAI 无 cache_control，靠端点前缀自动缓存；tools 不设缓存标记。
        """
        api_messages: list[dict] = []

        # 段1（稳定，前缀缓存受益）+ 段2 环境信息（变化）
        if payload.stable_prompt:
            api_messages.append({"role": "user", "content": payload.stable_prompt})
        if payload.env_segment:
            api_messages.append({"role": "user", "content": payload.env_segment})

        # 会话历史
        for msg in payload.messages:
            if msg.role == "tool":
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id or "",
                        "name": msg.name or "",
                        "content": msg.content,
                    }
                )
            elif msg.role == "assistant" and msg.tool_calls:
                # OpenAI assistant tool_calls 声明格式
                import json

                api_messages.append(
                    {
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
                    }
                )
            else:
                api_messages.append({"role": msg.role, "content": msg.content})

        # 轮次级 system-reminder（role=user，<system-reminder> 标签，瞬时不持久）
        for r in payload.reminders:
            api_messages.append({"role": "user", "content": r.content})

        kwargs: dict = {
            "model": api_model(self._model),
            "messages": api_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if payload.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in payload.tools
            ]

        write_request_record(payload, "openai", api_model(self._model), kwargs)

        # tool_calls 分片拼接状态：index -> {"id": ..., "name": ..., "arguments": ...}
        _tool_call_buffers: dict[int, dict] = {}
        _last_usage: TokenUsage | None = None

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
                            _tool_call_buffers[idx] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }
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

                # 捕获 usage（OpenAI 在最后一个 chunk 中返回），含 cached_tokens
                if chunk.usage:
                    _last_usage = _to_usage(chunk.usage)

            yield StreamEvent(done=True, usage=_last_usage)
        except OpenAIAPIError as e:
            yield StreamEvent(err=_wrap_openai_error(e))
        except Exception as e:  # noqa: BLE001 — 流式消费中任何异常都应包装为 ProviderError，不崩溃
            yield StreamEvent(err=ProviderError(f"OpenAI 请求失败: {e}"))


def _to_usage(u: object) -> TokenUsage:
    """解析 OpenAI usage → TokenUsage；cached_tokens 缺失按 0（N1 健壮解析）"""
    details = getattr(u, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details is not None else 0
    return TokenUsage(
        input_tokens=getattr(u, "prompt_tokens", 0) or 0,
        output_tokens=getattr(u, "completion_tokens", 0) or 0,
        cache_read_input_tokens=cached or 0,
    )


def _wrap_openai_error(e: OpenAIAPIError) -> PromptTooLongError | ProviderError:
    """OpenAI 400 错误中识别 PTL 关键词/code，wrap 为 PromptTooLongError 哨兵（保留 __cause__）。"""
    if getattr(e, "status_code", None) == 400:
        msg = str(e)
        if (
            getattr(e, "code", "") == "context_length_exceeded"
            or "maximum context length" in msg
        ):
            wrapped = PromptTooLongError(str(e))
            wrapped.__cause__ = e
            return wrapped
    return ProviderError(f"OpenAI API 错误: {e}")
