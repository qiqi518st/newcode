"""MCPConnection：单 server 会话的生命周期与调用，封装官方 mcp SDK 传输 + ClientSession。

关键设计：
- 每连接一个私有 AsyncExitStack（不共享，规避并发 enter 竞态），传输 / http client /
  ClientSession 全部进栈；上下文不在 connect_and_list 返回时退出，存活到 close()。
- 连接失败或被取消（含 manager 的 wait_for 超时取消）时立即收栈，防止已拉起的
  stdio 子进程泄漏（spec N7）。
- call_tool 把超时/协议错/非预期返回类型统一翻译成 ToolResult(status="error")，
  绝不向调用方抛 Python 异常（复用「不中断会话」契约，spec F7/F10）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import CallToolResult, Implementation, TextContent

from ..provider.base import ToolResult
from .config import ServerConfig
from .wrapper import McpTool, _non_text_warn_once, make_tool

# 工具调用超时（秒）：与连接超时同值 30s，内置不可配（spec F10）。
# 模块级变量（非字面常量）便于单测临时改小并 restore。
call_timeout: float = 30.0


class MCPStartupError(Exception):
    """单个 server 的连接/握手/列工具失败（由 MCPManager 捕获，不外抛启动）。"""


class MCPConnection:
    """单 server 会话句柄。"""

    def __init__(self, server: ServerConfig, client_version: str) -> None:
        self.server = server
        self._client_version = client_version
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack = AsyncExitStack()
        self._closed = False

    @property
    def server_name(self) -> str:
        return self.server.name

    async def connect_and_list(self) -> list[McpTool]:
        """打开传输 + 握手 + 列工具；失败抛 MCPStartupError（并收栈防泄漏）。"""
        if self._session is not None or self._closed:
            raise MCPStartupError(
                f"server {self.server.name} already connected or closed"
            )
        try:
            if self.server.type == "stdio":
                # env 与宿主环境合并后注入，server.env 覆盖同名宿主变量（spec F4）
                params = StdioServerParameters(
                    command=self.server.command,
                    args=list(self.server.args),
                    env={**os.environ, **self.server.env},
                )
                transport_ctx = stdio_client(params)
            else:
                # SDK 2.0：streamable_http_client 无 headers 参数，
                # 经官方 create_mcp_http_client（httpx2，MCP 默认超时/redirect）注入
                http_client = (
                    create_mcp_http_client(headers=dict(self.server.headers))
                    if self.server.headers
                    else None
                )
                if http_client is not None:
                    # AsyncClient 需上下文管理收尾，一并挂到私有栈
                    await self._stack.enter_async_context(http_client)
                transport_ctx = streamable_http_client(
                    self.server.url, http_client=http_client
                )
            transport = await self._stack.enter_async_context(transport_ctx)
            read_stream, write_stream = transport  # stdio / http 都 yield 2 元组
            session = await self._stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    client_info=Implementation(
                        name="mewcode", version=self._client_version
                    ),
                )
            )
            await session.initialize()
            listed = await session.list_tools()
            self._session = session
            tools = (
                make_tool(self.server.name, self, remote) for remote in listed.tools
            )
            return [t for t in tools if t is not None]
        except asyncio.CancelledError:
            # manager 的 wait_for 超时会取消本协程：先收栈再放行取消
            await self._safe_close_stack()
            self._closed = True
            raise
        except Exception as e:
            # 失败也要收掉已进入的上下文，防已拉起的子进程/连接泄漏（spec N7）
            await self._safe_close_stack()
            self._closed = True
            raise MCPStartupError(
                f"connect server {self.server.name} failed: {e}"
            ) from e

    async def call_tool(self, tool_name: str, arguments: dict) -> ToolResult:
        """调用远端工具；超时/协议错/非预期返回统一转 ToolResult(status="error")。"""
        if self._session is None:
            return ToolResult(
                status="error", error=f"MCP server {self.server.name} 未连接"
            )
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments=arguments or {}),
                call_timeout,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                status="error", error=f"MCP 工具调用超时 ({call_timeout:.0f}s)"
            )
        except Exception as e:  # noqa: BLE001 -- 必须吞掉 SDK 任意异常转结构化错误（spec F7 契约）
            return ToolResult(status="error", error=f"MCP 工具调用失败: {e}")
        # SDK 2.0：call_tool 返回 CallToolResult | InputRequiredResult | Result 联合
        if not isinstance(result, CallToolResult):
            return ToolResult(status="error", error="MCP 工具返回非预期结果类型")
        texts: list[str] = []
        has_non_text = False
        for block in result.content:
            if isinstance(block, TextContent):
                texts.append(block.text)
            else:
                has_non_text = True
        if has_non_text:
            full = f"mcp__{self.server.name}__{tool_name}"
            if full not in _non_text_warn_once:
                _non_text_warn_once.add(full)
                print(
                    f"[mcp] warn: tool {full} returned non-text content blocks (dropped)",
                    file=sys.stderr,
                )
        joined = "\n".join(texts)
        if result.is_error:
            return ToolResult(status="error", error=joined or "MCP 远端工具返回错误")
        return ToolResult(status="ok", output=joined)

    async def close(self) -> None:
        """退出私有栈（传输 / session / http client 一并收尾）；自身不加超时。

        MCPManager.close 的单层 5s 兜底已覆盖本方法卡住的情形（spec F11）。
        """
        if self._closed:
            return
        self._closed = True
        try:
            await self._stack.aclose()
        except Exception as e:  # noqa: BLE001 -- close 收尾不得向上抛（spec N7）
            print(
                f"[mcp] warn: close server {self.server.name} failed: {e}",
                file=sys.stderr,
            )

    async def _safe_close_stack(self) -> None:
        """收栈且吞掉收栈自身的异常（清理路径不应再抛）。"""
        try:
            await self._stack.aclose()
        except Exception:  # noqa: S110 BLE001 -- 清理路径有意吞掉，防止掩盖原始错误
            pass
