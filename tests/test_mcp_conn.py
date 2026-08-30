"""MCPConnection 单测（ch07 T3）：用 fake session / fake transport 驱动真实代码路径。

防的 bug：
- 曾可能出现 TextContent 文本拼进 error 而非 output -> 锁定映射方向（spec F7）。
- 曾可能出现非 text 块把 stderr 告警刷屏 -> 同 full_name 只告警一次。
- 曾可能出现协议错向 Agent Loop 抛 Python 异常中断会话 -> 全部转 ToolResult(error)。
- 曾可能出现连接失败不收栈、stdio 子进程泄漏 -> connect_and_list 失败路径收栈（spec N7）。
"""

from types import SimpleNamespace

import pytest

import mewcode.mcp.conn as conn_mod
from mewcode.mcp.config import ServerConfig
from mewcode.mcp.conn import MCPConnection, MCPStartupError


def _stdio_server(name: str = "demo") -> ServerConfig:
    return ServerConfig(name=name, type="stdio", command="fake-cmd")


class FakeSession:
    """白盒注入 conn._session 的假 ClientSession：只实现 call_tool。"""

    def __init__(
        self, result=None, exc: Exception | None = None, hang: bool = False
    ) -> None:
        self._result = result
        self._exc = exc
        self._hang = hang
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, dict(arguments or {})))
        if self._hang:
            import asyncio

            await asyncio.Event().wait()
        if self._exc is not None:
            raise self._exc
        return self._result


def _text(text: str):
    from mcp.types import TextContent

    return TextContent(type="text", text=text)


def _image():
    """真非 text 块：CallToolResult.content 是 pydantic 校验的 list[ContentBlock]，
    塞自定义假对象会被 ValidationError 拒掉，必须用真 ImageContent。"""
    from mcp.types import ImageContent

    return ImageContent(data="aGk=", mimeType="image/png")


def _ct_result(blocks, is_error: bool = False):
    from mcp.types import CallToolResult

    return CallToolResult(content=blocks, is_error=is_error)


def _conn(server: ServerConfig | None = None) -> MCPConnection:
    return MCPConnection(server or _stdio_server(), "0.7.0-test")


# ── call_tool 各分支 ──────────────────────────────────────


@pytest.mark.anyio
async def test_call_tool_success_joins_text_blocks():
    """多个 TextContent 按序拼进 output，is_error=False（spec F7）。"""
    c = _conn()
    c._session = FakeSession(
        result=_ct_result([_text("第一段"), _text("第二段")], is_error=False)
    )
    r = await c.call_tool("echo", {"x": 1})
    assert r.status == "ok"
    assert r.output == "第一段\n第二段"
    assert r.error == ""


@pytest.mark.anyio
async def test_call_tool_remote_iserror_maps_to_error():
    """远端 is_error=True -> status=error、文本进 error（spec F7）。"""
    c = _conn()
    c._session = FakeSession(result=_ct_result([_text("boom")], is_error=True))
    r = await c.call_tool("boom_tool", {})
    assert r.status == "error"
    assert "boom" in r.error
    assert r.output == ""


@pytest.mark.anyio
async def test_call_tool_non_text_dropped_warn_once(capsys):
    """非 text 块丢弃不进 output；同 full_name 多次调用只告警一次。"""
    c = _conn()
    c._session = FakeSession(result=_ct_result([_text("t"), _image(), _image()]))
    r1 = await c.call_tool("img_tool", {})
    assert r1.status == "ok" and r1.output == "t"
    await c.call_tool("img_tool", {})
    err = capsys.readouterr().err
    assert err.count("non-text content blocks") == 1
    assert "mcp__demo__img_tool" in err


@pytest.mark.anyio
async def test_call_tool_exception_translated_not_raised():
    """session.call_tool 抛异常 -> ToolResult(error)，不向调用方抛（spec F7/F10）。"""
    c = _conn()
    c._session = FakeSession(exc=RuntimeError("连接断开"))
    r = await c.call_tool("t", {})
    assert r.status == "error"
    assert "MCP 工具调用失败" in r.error and "连接断开" in r.error


@pytest.mark.anyio
async def test_call_tool_timeout_translated(monkeypatch):
    """超时分支：call_timeout 临时改小 + 挂起 fake -> 错误结果不挂死。"""
    monkeypatch.setattr(conn_mod, "call_timeout", 0.1)
    c = _conn()
    c._session = FakeSession(hang=True)
    r = await c.call_tool("slow", {})
    assert r.status == "error"
    assert "超时" in r.error


@pytest.mark.anyio
async def test_call_tool_unexpected_result_type():
    """SDK 2.0 联合返回中的非 CallToolResult（如 InputRequiredResult）-> 错误结果。"""
    c = _conn()
    c._session = FakeSession(result=SimpleNamespace())  # 不是 CallToolResult
    r = await c.call_tool("t", {})
    assert r.status == "error"
    assert "非预期结果类型" in r.error


@pytest.mark.anyio
async def test_call_tool_before_connect():
    """未连接直接调用 -> 结构化错误而非 AttributeError。"""
    c = _conn()
    r = await c.call_tool("t", {})
    assert r.status == "error"
    assert "未连接" in r.error


# ── connect_and_list 包装路径（fake transport + fake ClientSession）──


class _FakeTransportCtx:
    async def __aenter__(self):
        return (object(), object())

    async def __aexit__(self, *exc):
        return False


class _FakeSessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


@pytest.mark.anyio
async def test_connect_and_list_wraps_tools(monkeypatch):
    """拿到 list_tools 结果后按 make_tool 包装，名字前缀正确，非法名跳过（spec F7/F8）。"""
    remote_ok = SimpleNamespace(
        name="echo",
        description="回显",
        input_schema={"type": "object"},
        annotations=None,
    )
    remote_bad = SimpleNamespace(
        name="bad/name",
        description=None,
        input_schema=None,
        annotations=None,
    )
    fake_session = SimpleNamespace(
        initialize=None,  # 占位，下方替换为 async 函数
        list_tools=None,
    )

    async def _init():
        return None

    async def _list():
        return SimpleNamespace(tools=[remote_ok, remote_bad])

    fake_session.initialize = _init
    fake_session.list_tools = _list

    monkeypatch.setattr(conn_mod, "stdio_client", lambda params: _FakeTransportCtx())
    monkeypatch.setattr(
        conn_mod,
        "ClientSession",
        lambda read, write, client_info=None: _FakeSessionCtx(fake_session),
    )

    c = _conn()
    tools = await c.connect_and_list()
    assert [t.name for t in tools] == ["mcp__demo__echo"]
    assert tools[0].description == "回显"
    # session 已置位，后续 call_tool 走 fake
    assert c._session is fake_session


@pytest.mark.anyio
async def test_connect_and_list_failure_raises_and_closes_stack(monkeypatch):
    """连接失败 -> MCPStartupError 且收栈（session 不置位、栈已退空）（spec N7）。"""
    monkeypatch.setattr(conn_mod, "stdio_client", lambda params: _FakeTransportCtx())

    class _InitBoom:
        async def initialize(self):
            raise RuntimeError("握手失败")

        async def list_tools(self):
            raise AssertionError("不应到达")

    monkeypatch.setattr(
        conn_mod,
        "ClientSession",
        lambda read, write, client_info=None: _FakeSessionCtx(_InitBoom()),
    )

    c = _conn()
    with pytest.raises(MCPStartupError, match="connect server demo failed"):
        await c.connect_and_list()
    assert c._session is None
    assert c._closed is True  # 失败后标记关闭，close() 幂等无害
    await c.close()  # 不抛（幂等）
