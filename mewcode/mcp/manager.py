"""MCPManager：多 server 生命周期编排--并发启动、收集排序、统一关闭、失败隔离。

- start_all：asyncio.gather 并发连接所有 server；单 server 失败/超时只跳过自身
  （spec F9/N1）；同名工具在收集阶段告警并保留后入者（spec F8）。
- close：并发关全部连接，整体 5s 兜底，不因个别 server 卡死阻塞退出（spec F11/N7）。
- 本模块不可失败：start_all/close 只产告警，绝不抛（保证 main 启动/退出不被阻断）。
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field

from .config import ServerConfig
from .conn import MCPConnection, call_timeout  # 统一超时入口，供调用方复用
from .wrapper import McpTool

# 连接超时（秒）：每个 server 的整个启动序列受此约束（spec F9，内置不可配）
connect_timeout: float = 30.0
# 退出关闭整体兜底超时（秒）（spec F11）
close_timeout: float = 5.0

__all__ = [
    "MCPManager",
    "StartupSummary",
    "call_timeout",
    "close_timeout",
    "connect_timeout",
]


@dataclass
class StartupSummary:
    """MCP 启动结果摘要（供装配处打印可观测摘要，spec N5）。"""

    connected: list[tuple[str, int]] = field(default_factory=list)
    """[(server_name, tool_count), ...]，按 server 名排序"""
    failed: list[tuple[str, str]] = field(default_factory=list)
    """[(server_name, reason), ...]，按 server 名排序"""
    total_tools: int = 0

    @property
    def is_empty(self) -> bool:
        """无任何 server 被尝试（既无成功也无失败）——打摘要时可跳过。"""
        return not self.connected and not self.failed


def _format_summary(summary: StartupSummary) -> str:
    """把启动摘要格式化成单行 stderr 文案（spec N5 可观测性）。"""
    parts = [f"{n}({c} tools)" for n, c in summary.connected]
    if summary.failed:
        parts.extend(f"{n}:failed" for n, _ in summary.failed)
    return (
        "[mcp] startup: " + ", ".join(parts) + f" | total {summary.total_tools} tools"
    )


class MCPManager:
    """生命周期编排器：与 Registry 解耦，只产工具，注册由装配处负责。"""

    def __init__(self, servers: dict[str, ServerConfig], client_version: str) -> None:
        self._servers = servers
        self._client_version = client_version
        self._connections: list[MCPConnection] = []
        self._tools: list[McpTool] = []
        self._failures: dict[str, str] = {}

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
            reason = f"timeout after {connect_timeout}s"
            self._failures[name] = reason
            print(f"[mcp] warn: connect server {name} {reason}", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001 -- 单 server 任意失败只跳过自身（spec F9 隔离契约）
            reason = str(e) or type(e).__name__
            self._failures[name] = reason
            print(
                f"[mcp] warn: connect server {name} failed: {reason}", file=sys.stderr
            )
            return None
        self._connections.append(conn)
        return tools

    async def start_all(self) -> StartupSummary:
        """并发连接所有 server，收集工具并按 full_name 稳定排序。本方法不可失败。

        返回启动摘要（spec N5），供装配处打印可观测信息。
        """
        results = await asyncio.gather(
            *[self._start_one(n, s) for n, s in self._servers.items()],
            return_exceptions=True,
        )
        # 按 server 名记录每个连接产出的工具数（用于摘要）
        per_server_counts: dict[str, int] = {}
        by_name: dict[str, McpTool] = {}
        for (name, _srv), res in zip(self._servers.items(), results):
            if not isinstance(res, list):
                continue  # 失败/超时（None）或异常，已在 _start_one 记录
            per_server_counts[name] = len(res)
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
        summary = StartupSummary(
            connected=sorted(per_server_counts.items()),
            failed=sorted(self._failures.items()),
            total_tools=len(self._tools),
        )
        return summary

    def tools(self) -> list[McpTool]:
        """返回按 full_name 排序的工具列表副本（防外部修改）。"""
        return list(self._tools)

    @staticmethod
    def format_summary(summary: StartupSummary) -> str:
        """格式化摘要为单行文案（供 main 打印）。"""
        return _format_summary(summary)

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
