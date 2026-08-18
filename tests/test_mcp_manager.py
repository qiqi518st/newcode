"""MCPManager 单测（ch07 T4）：fake 连接驱动真实 start_all/tools/close 路径。

防的 bug：
- 曾可能出现某 server 超时拖死整个 start_all -> 并发 + wait_for 隔离（spec F9/N1）。
- 曾可能出现 close 卡死阻塞程序退出 -> 5s 兜底 + 小超时验证不挂（spec F11/N7）。
- 曾可能出现 tools() 顺序依赖 task 完成序 -> 稳定排序断言（spec F9）。
- 曾可能出现同名工具静默覆盖 -> 收集阶段告警 + 后入者保留（spec F8）。
"""

import asyncio
from typing import ClassVar

import pytest

import mewcode.mcp.manager as manager_mod
from mewcode.mcp.config import ServerConfig
from mewcode.mcp.manager import MCPManager
from mewcode.mcp.wrapper import McpTool


def _server(name: str) -> ServerConfig:
    return ServerConfig(name=name, type="stdio", command="fake")


def _tool(full_name: str) -> McpTool:
    """构造带指定 full_name 的最小 McpTool（caller 不会被调用）。"""
    _server, _, remote = full_name.partition("__")
    return McpTool(
        caller=None,  # type: ignore[arg-type]
        full_name=full_name,
        remote_name=remote,
        description="d",
        parameters={"type": "object"},
        read_only=False,
    )


class FakeConn:
    """按 server 名分派的假 MCPConnection（monkeypatch 注入 manager 命名空间）。

    类属性作为跨测试共享的注入开关，fixture 里重置。
    """

    fail: ClassVar[set[str]] = set()
    hang: ClassVar[set[str]] = set()
    tools_map: ClassVar[dict[str, list[McpTool]]] = {}

    def __init__(self, server: ServerConfig, version: str) -> None:
        self.server = server
        self.version = version
        self.closed = False

    async def connect_and_list(self):
        name = self.server.name
        if name in FakeConn.hang:
            await asyncio.Event().wait()
        if name in FakeConn.fail:
            raise RuntimeError("boom")
        return list(FakeConn.tools_map.get(name, []))

    async def close(self):
        if self.server.name == "close-hang":
            await asyncio.Event().wait()
        self.closed = True


@pytest.fixture
def fake_conn(monkeypatch):
    FakeConn.fail = set()
    FakeConn.hang = set()
    FakeConn.tools_map = {}
    monkeypatch.setattr(manager_mod, "MCPConnection", FakeConn)
    yield FakeConn
    FakeConn.fail = set()
    FakeConn.hang = set()
    FakeConn.tools_map = {}


def _no_dangling_tasks(before: set[asyncio.Task]) -> None:
    """对比式悬挂 task 断言：anyio 的 asyncio backend 在测试期间有自身 runner
    task（全量差集永远非空会误报），因此只断言「测试新增的 task」为空。"""
    assert not asyncio.all_tasks() - before


def _task_baseline() -> set[asyncio.Task]:
    return asyncio.all_tasks()


@pytest.mark.anyio
async def test_all_success_sorted_by_full_name(fake_conn):
    """工具按 full_name 稳定排序，与 gather 完成顺序无关（spec F9）。"""
    before = _task_baseline()
    fake_conn.tools_map = {
        "b": [_tool("mcp__b__x1"), _tool("mcp__b__x0")],
        "a": [_tool("mcp__a__z")],
    }
    m = MCPManager({"b": _server("b"), "a": _server("a")}, "0.7.0-test")
    summary = await m.start_all()
    assert [t.full_name for t in m.tools()] == [
        "mcp__a__z",
        "mcp__b__x0",
        "mcp__b__x1",
    ]
    assert len(m.connections) == 2
    # spec N5：启动摘要记录成功 server 与工具总数
    assert dict(summary.connected) == {"a": 1, "b": 2}
    assert summary.failed == []
    assert summary.total_tools == 3
    assert (
        MCPManager.format_summary(summary)
        == "[mcp] startup: a(1 tools), b(2 tools) | total 3 tools"
    )
    _no_dangling_tasks(before)


@pytest.mark.anyio
async def test_failure_isolated_other_server_kept(fake_conn, capsys):
    """单 server 连接失败只跳过自身 + 告警；其它照常收集（spec F9/N1）。"""
    before = _task_baseline()
    fake_conn.fail = {"bad"}
    fake_conn.tools_map = {"good": [_tool("mcp__good__alpha")]}
    m = MCPManager({"bad": _server("bad"), "good": _server("good")}, "v")
    summary = await m.start_all()
    assert [t.full_name for t in m.tools()] == ["mcp__good__alpha"]
    assert [c.server.name for c in m.connections] == ["good"]
    # 摘要含失败项（spec N5）
    assert dict(summary.connected) == {"good": 1}
    assert [n for n, _ in summary.failed] == ["bad"]
    assert (
        MCPManager.format_summary(summary)
        == "[mcp] startup: good(1 tools), bad:failed | total 1 tools"
    )
    err = capsys.readouterr().err
    # _start_one 的逐项告警（摘要行由装配处打印，非 manager 职责）
    assert "connect server bad failed" in err
    _no_dangling_tasks(before)


@pytest.mark.anyio
async def test_connect_timeout_bounded(fake_conn, monkeypatch, capsys):
    """挂起 server 受 connect_timeout 约束，start_all 不被拖死（spec F9/N1）。"""
    before = _task_baseline()
    monkeypatch.setattr(manager_mod, "connect_timeout", 0.2)
    fake_conn.hang = {"slow"}
    fake_conn.tools_map = {"ok": [_tool("mcp__ok__t")]}
    m = MCPManager({"slow": _server("slow"), "ok": _server("ok")}, "v")
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await m.start_all()
    assert loop.time() - t0 < 2.0
    assert [t.full_name for t in m.tools()] == ["mcp__ok__t"]
    assert "connect server slow timeout" in capsys.readouterr().err
    _no_dangling_tasks(before)


@pytest.mark.anyio
async def test_real_bad_command_server_isolated(monkeypatch, capsys):
    """真坏 command（SDK stdio_client 真实失败路径）+ 假成功组合：失败跳过、成功保留。"""
    import mewcode.mcp.conn as conn_mod

    class RealForBadFakeForGood:
        def __new__(cls, server, version):
            if server.name == "good":
                return FakeConn(server, version)
            return conn_mod.MCPConnection(server, version)

    FakeConn.tools_map = {"good": [_tool("mcp__good__alpha")]}
    monkeypatch.setattr(manager_mod, "MCPConnection", RealForBadFakeForGood)
    monkeypatch.setattr(manager_mod, "connect_timeout", 5.0)
    bad = ServerConfig(name="bad", type="stdio", command="/no/such/bin-xyz")
    m = MCPManager({"bad": bad, "good": _server("good")}, "v")
    await m.start_all()
    assert [t.full_name for t in m.tools()] == ["mcp__good__alpha"]
    assert "connect server bad" in capsys.readouterr().err


@pytest.mark.anyio
async def test_duplicate_tool_warn_and_later_wins(fake_conn, capsys):
    """同名工具收集阶段告警一次、后入者保留（spec F8）。"""
    before = _task_baseline()
    fake_conn.tools_map = {
        "a": [_tool("mcp__a__dup")],
        "b": [_tool("mcp__a__dup")],  # 越界构造同名（模拟同 server 自报重名）
    }
    m = MCPManager({"a": _server("a"), "b": _server("b")}, "v")
    await m.start_all()
    tools = m.tools()
    assert [t.full_name for t in tools] == ["mcp__a__dup"]  # 只留一个
    assert "duplicate tool mcp__a__dup" in capsys.readouterr().err
    _no_dangling_tasks(before)


@pytest.mark.anyio
async def test_empty_servers_start_and_close_noop():
    before = _task_baseline()
    m = MCPManager({}, "v")
    summary = await m.start_all()
    assert m.tools() == []
    # spec N5：无 server 被尝试 -> 摘要空 -> 装配处不打印（避免噪音）
    assert summary.is_empty is True
    assert MCPManager.format_summary(summary) == "[mcp] startup:  | total 0 tools"
    await m.close()  # 立即返回不抛
    _no_dangling_tasks(before)


@pytest.mark.anyio
async def test_close_timeout_bounded(fake_conn, monkeypatch, capsys):
    """个别连接 close 卡住不阻塞整体退出（spec F11/N7）。"""
    before = _task_baseline()
    monkeypatch.setattr(manager_mod, "close_timeout", 0.2)
    fake_conn.tools_map = {"close-hang": [_tool("mcp__close-hang__t")], "ok": []}
    m = MCPManager({"close-hang": _server("close-hang"), "ok": _server("ok")}, "v")
    await m.start_all()
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await m.close()
    assert loop.time() - t0 < 2.0
    assert "close timeout" in capsys.readouterr().err
    _no_dangling_tasks(before)


@pytest.mark.anyio
async def test_close_closes_all_connections(fake_conn):
    before = _task_baseline()
    fake_conn.tools_map = {"a": [_tool("mcp__a__t")], "b": []}
    m = MCPManager({"a": _server("a"), "b": _server("b")}, "v")
    await m.start_all()
    conns = m.connections
    await m.close()
    assert all(c.closed for c in conns)
    _no_dangling_tasks(before)
