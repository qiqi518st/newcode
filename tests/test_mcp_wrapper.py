"""McpTool 适配器单测（ch07 T2）。

防的 bug：
- 拼接名含禁用字符曾可能直接注册 -> provider 拒收整个工具列表（spec F8）。
- annotations 为 None 时属性访问曾可能抛 AttributeError（spec F7 None-safe）。
- 空 inputSchema 透传 -> provider 报 schema 错（兜底 {"type":"object"}）。
- execute 曾可能不透传 arguments 或吞掉 caller 返回的 ToolResult。
"""

from dataclasses import dataclass

import pytest

from mewcode.mcp.wrapper import make_tool
from mewcode.provider.base import ToolResult


@dataclass
class FakeAnnotations:
    read_only_hint: bool | None = None


@dataclass
class FakeRemote:
    """镜像 SDK 2.0 mcp.types.Tool 的字段名（snake_case）。"""

    name: str
    description: str | None = None
    input_schema: dict | None = None
    annotations: FakeAnnotations | None = None


class StubSession:
    """CallerSession stub：记录调用并返回预设结果。"""

    def __init__(self, result: ToolResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> ToolResult:
        self.calls.append((name, arguments))
        return self.result


def _stub() -> StubSession:
    return StubSession(ToolResult(status="ok", output="done"))


# ── 命名拼接与禁用字符 ────────────────────────────────────


def test_name_prefix_format():
    tool = make_tool("github", _stub(), FakeRemote(name="search_issues"))
    assert tool is not None
    assert tool.name == "mcp__github__search_issues"
    assert tool.full_name == "mcp__github__search_issues"


def test_illegal_char_tool_skipped_with_warning(capsys):
    """工具名含 '/' -> 跳过 + 告警（spec F8）。"""
    tool = make_tool("demo", _stub(), FakeRemote(name="a/b"))
    assert tool is None
    assert "skip tool mcp__demo__a/b" in capsys.readouterr().err


def test_illegal_char_server_name_skipped():
    """server 名含 '@' 也会使拼接名非法 -> 跳过。"""
    assert make_tool("bad@server", _stub(), FakeRemote(name="x")) is None


# ── 描述 / schema / 只读性 ────────────────────────────────


def test_description_passthrough():
    tool = make_tool("s", _stub(), FakeRemote(name="t", description="远端描述"))
    assert tool is not None
    assert tool.description == "远端描述"


def test_description_fallback_mentions_server():
    tool = make_tool("myserver", _stub(), FakeRemote(name="t", description=None))
    assert tool is not None
    assert "myserver" in tool.description


def test_schema_passthrough():
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    tool = make_tool("s", _stub(), FakeRemote(name="t", input_schema=schema))
    assert tool is not None
    assert tool.parameters == schema


def test_empty_schema_fallback():
    for empty in (None, {}):
        tool = make_tool("s", _stub(), FakeRemote(name="t", input_schema=empty))
        assert tool is not None
        assert tool.parameters == {"type": "object"}


def test_read_only_three_states():
    """readOnlyHint=True -> True；False/None/annotations 缺失 -> False（安全默认）。"""
    tool = make_tool(
        "s",
        _stub(),
        FakeRemote(name="t", annotations=FakeAnnotations(read_only_hint=True)),
    )
    assert tool is not None and tool.read_only is True
    for ann in (
        FakeAnnotations(read_only_hint=False),
        FakeAnnotations(read_only_hint=None),
        None,
    ):
        tool = make_tool("s", _stub(), FakeRemote(name="t", annotations=ann))
        assert tool is not None and tool.read_only is False


# ── execute 转发（驱动真实 McpTool.execute 路径）──────────


@pytest.mark.anyio
async def test_execute_forwards_remote_name_and_arguments():
    stub = StubSession(ToolResult(status="ok", output="hi"))
    tool = make_tool("srv", stub, FakeRemote(name="echo"))
    assert tool is not None
    result = await tool.execute({"msg": "x"})
    assert result.status == "ok" and result.output == "hi"
    assert stub.calls == [("echo", {"msg": "x"})]


@pytest.mark.anyio
async def test_execute_empty_arguments_normalized():
    stub = StubSession(ToolResult(status="error", error="e"))
    tool = make_tool("srv", stub, FakeRemote(name="ping"))
    assert tool is not None
    await tool.execute({})
    await tool.execute(None)  # type: ignore[arg-type]
    assert stub.calls == [("ping", {}), ("ping", {})]
