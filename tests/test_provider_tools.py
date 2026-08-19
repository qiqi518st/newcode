"""Provider 工具解析测试"""

import json
from types import SimpleNamespace

import pytest

from mewcode.prompt.assembler import PromptPayload
from mewcode.provider.anthropic import AnthropicProvider
from mewcode.provider.base import Message, ToolCall
from mewcode.provider.openai import OpenAIProvider


class TestAnthropicToolParsing:
    """模拟 Anthropic SDK 流事件序列"""

    @pytest.mark.anyio
    async def test_parse_tool_use(self):
        # 模拟 Anthropic stream 事件序列
        events = [
            {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "name": "read_file",
                    "id": "tu_123",
                },
            },
            {
                "type": "content_block_delta",
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"path": "main.py"}',
                },
            },
            {"type": "content_block_stop"},
            {"type": "message_stop"},
        ]
        # 这里只验证 ToolCall 组装逻辑，不直接调用 Provider
        partial_json = ""
        tool_name = None
        tool_use_id = None
        for ev in events:
            if ev["type"] == "content_block_start":
                tool_name = ev["content_block"]["name"]
                tool_use_id = ev["content_block"]["id"]
            elif ev["type"] == "content_block_delta":
                partial_json += ev["delta"]["partial_json"]
            elif ev["type"] == "content_block_stop":
                args = json.loads(partial_json)
                tc = ToolCall(
                    tool_name=tool_name, arguments=args, tool_use_id=tool_use_id
                )
                assert tc.tool_name == "read_file"
                assert tc.arguments == {"path": "main.py"}
                assert tc.tool_use_id == "tu_123"


class TestAnthropicToolMessageAssembly:
    @pytest.mark.anyio
    async def test_multiple_tool_results_are_one_immediate_user_message(self, tmp_path):
        """防回归：多个 tool_use 必须紧邻一个包含全部 tool_result 的 user 消息。"""

        class FakeStream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if hasattr(self, "done"):
                    raise StopAsyncIteration
                self.done = True
                return SimpleNamespace(type="message_stop")

            async def close(self):
                pass

        class FakeMessages:
            def __init__(self):
                self.kwargs = None

            async def create(self, **kwargs):
                self.kwargs = kwargs
                return FakeStream()

        provider = object.__new__(AnthropicProvider)
        provider._model = "test-model"
        provider._thinking = False
        client_messages = FakeMessages()
        provider._client = SimpleNamespace(messages=client_messages)
        payload = PromptPayload(
            stable_prompt="stable",
            env_segment="",
            messages=[
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[
                        {"id": "call_a", "name": "read_file", "arguments": {}},
                        {"id": "call_b", "name": "list_files", "arguments": {}},
                    ],
                ),
                Message(role="tool", content="a", tool_use_id="call_a"),
                Message(role="tool", content="b", tool_use_id="call_b"),
            ],
            trace_context={"path": str(tmp_path / "anthropic.json")},
        )

        [event async for event in provider.stream(payload)]

        messages = client_messages.kwargs["messages"]
        assert messages[-1] == {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_a",
                    "content": "a",
                    "is_error": False,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "call_b",
                    "content": "b",
                    "is_error": False,
                },
            ],
        }
        trace = json.loads((tmp_path / "anthropic.json").read_text(encoding="utf-8"))
        assert trace["provider_request"]["messages"] == messages


class TestOpenAIToolParsing:
    """模拟 OpenAI SDK 流事件序列"""

    @pytest.mark.anyio
    async def test_parse_function_call(self):
        # 模拟 OpenAI tool_calls 分片
        chunks = [
            {
                "id": "fc_123",
                "function": {"name": "read_file", "arguments": '{"path": '},
            },
            {"id": "fc_123", "function": {"arguments": '"main.py"}'}},
        ]

        buffers = {}
        for tc in chunks:
            idx = 0  # 简化为单 index
            if idx not in buffers:
                buffers[idx] = {"id": "", "name": "", "arguments": ""}
            buf = buffers[idx]
            if tc.get("id"):
                buf["id"] = tc["id"]
            if tc.get("function", {}).get("name"):
                buf["name"] = tc["function"]["name"]
            if tc.get("function", {}).get("arguments"):
                buf["arguments"] += tc["function"]["arguments"]

        buf = buffers[0]
        args = json.loads(buf["arguments"])
        tc = ToolCall(tool_name=buf["name"], arguments=args, tool_call_id=buf["id"])
        assert tc.tool_name == "read_file"
        assert tc.arguments == {"path": "main.py"}
        assert tc.tool_call_id == "fc_123"

    @pytest.mark.anyio
    async def test_provider_trace_contains_final_messages(self, tmp_path):
        class FakeStream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        class FakeCompletions:
            def __init__(self):
                self.kwargs = None

            async def create(self, **kwargs):
                self.kwargs = kwargs
                return FakeStream()

        completions = FakeCompletions()
        provider = object.__new__(OpenAIProvider)
        provider._model = "test-model"
        provider._client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        payload = PromptPayload(
            stable_prompt="stable",
            env_segment="",
            messages=[Message(role="user", content="hello")],
            trace_context={"path": str(tmp_path / "openai.json")},
        )

        [event async for event in provider.stream(payload)]

        trace = json.loads((tmp_path / "openai.json").read_text(encoding="utf-8"))
        assert trace["provider_request"]["messages"] == completions.kwargs["messages"]
