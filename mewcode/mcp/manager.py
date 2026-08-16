"""MCPManager：多 server 生命周期编排--并发启动、收集排序、统一关闭、失败隔离。

- start_all：asyncio.gather 并发连接所有 server；单 server 失败/超时只跳过自身
  （spec F9/N1）；同名工具在收集阶段告警并保留后入者（spec F8）。
- close：并发关全部连接，整体 5s 兜底，不因个别 server 卡死阻塞退出（spec F11/N7）。
- 本模块不可失败：start_all/close 只产告警，绝不抛（保证 main 启动/退出不被阻断）。
"""

from __future__ import annotations

import asyncio
import sys

from .config import ServerConfig
from .conn import MCPConnection, call_timeout  # 统一超时入口，供调用方复用
from .wrapper import McpTool

# 连接超时（秒）：每个 server 的整个启动序列受此约束（spec F9，内置不可配）
connect_timeout: float = 30.0
# 退出关闭整体兜底超时（秒）（spec F11）
close_timeout: float = 5.0

__all__ = [
    "MCPManager",
    "call_timeout",
    "close_timeout",
    "connect_timeout",
]


class MCPManager:
    """生命周期编排器：与 Registry 解耦，只产工具，注册由装配处负责。"""

    def __init__(self, servers: dict[str, ServerConfig], client_version: str) -> None:
        self._servers = servers
        self._client_version = client_version
        self._connections: list[MCPConnection] = []
        self._tools: list[McpTool] = []

    @property
    def connections(self) -> list[MCPConnection]:
        """成功建立的连接（测试用只读视图）。"""
        return list(self._connections)

    async def _start_one(self, name: str, srv: ServerConfig) -> list[McpTool] | None:
        """连接单个 server；任何失败/超时只告警并返回 None（不抛，spec F9/N1）。"""
        conn = MCPConnection(srv, self._client_version)
        try:
            tools = await asyncio.wait_for(conn.connect_and_list(), connect_timeout)
        except asyncio.TimeoutError:
            print(
                f"[mcp] warn: connect server {name} timeout after {connect_timeout}s",
                file=sys.stderr,
            )
            return None
        except Exception as e:  # noqa: BLE001 -- 单 server 任意失败只跳过自身（spec F9 隔离契约）
            print(
                f"[mcp] warn: connect server {name} failed: {e}",
                file=sys.stderr,
            )
            return None
        self._connections.append(conn)
        return tools

    async def start_all(self) -> None:
        """并发连接所有 server，收集工具并按 full_name 稳定排序。本方法不可失败。"""
        results = await asyncio.gather(
            *[self._start_one(n, s) for n, s in self._servers.items()],
            return_exceptions=True,
        )
        by_name: dict[str, McpTool] = {}
        for res in results:
            if not isinstance(res, list):
                continue  # 失败/超时的 server（None）或异常，已在 _start_one 告警
            for tool in res:
                if tool.full_name in by_name:
                    # spec F8：同名工具（同 server 自报多个同名）后入者保留 + 告警
                    print(
                        f"[mcp] warn: duplicate tool {tool.full_name}, "
                        "later registration overrides earlier",
                        file=sys.stderr,
                    )
                by_name[tool.full_name] = tool
        self._tools = [by_name[k] for k in sorted(by_name)]

    def tools(self) -> list[McpTool]:
        """返回按 full_name 排序的工具列表副本（防外部修改）。"""
        return list(self._tools)

    async def close(self) -> None:
        """并发关全部连接；整体 5s 兜底，超时放弃未关完的（不再等）。"""
        if not self._connections:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(c.close() for c in self._connections),
                    return_exceptions=True,
                ),
                close_timeout,
            )
        except asyncio.TimeoutError:
            print(
                f"[mcp] warn: close timeout ({close_timeout}s), some sessions may leak",
                file=sys.stderr,
            )
