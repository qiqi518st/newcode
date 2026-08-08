"""Provider 工具解析测试"""

import json
import pytest
from mewcode.provider.base import StreamEvent, ToolCall, Message, ToolDefinition


class TestAnthropicToolParsing:
    """模拟 Anthropic SDK 流事件序列"""

    @pytest.mark.anyio
    async def test_parse_tool_use(self):
        # 模拟 Anthropic stream 事件序列
        events = [
            {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "read_file", "id": "tu_123"}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"path": "main.py"}'}},
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
                tc = ToolCall(tool_name=tool_name, arguments=args, tool_use_id=tool_use_id)
                assert tc.tool_name == "read_file"
                assert tc.arguments == {"path": "main.py"}
                assert tc.tool_use_id == "tu_123"


class TestOpenAIToolParsing:
    """模拟 OpenAI SDK 流事件序列"""

    @pytest.mark.anyio
    async def test_parse_function_call(self):
        # 模拟 OpenAI tool_calls 分片
        chunks = [
            {"id": "fc_123", "function": {"name": "read_file", "arguments": '{"path": '}},
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
