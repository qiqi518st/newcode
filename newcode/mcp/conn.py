"""MCPConnection：单 server 会话的生命周期与调用，封装官方 mcp SDK 传输 + ClientSession。

关键设计：
- 传输 / session / http client 全部在一个长寿命 holder task 内经一个 AsyncExitStack 持有。
  holder 在 connect_and_list 返回后继续存活（停在 stop event 上），保持上下文不退出；
  close() 通过取消 holder task 触发上下文在 **holder 自己的 task 内** 退出。
  这规避了 anyio cancel scope「跨 task 退出」错误（stdio_client 用 anyio 实现，
  其 cancel scope 绑定进入它的 task；跨 task aclose 会抛 RuntimeError，spec N7）。
- connect_and_list 仅等 holder 完成 initialize + list_tools 后返回工具列表；
  连接失败/被取消（含 manager 的 wait_for 超时）时取消 holder 并等其收尾。
- call_tool 把超时/协议错/非预期返回类型统一翻译成 ToolResult(status="error")，
  绝不向调用方抛 Python 异常（复用「不中断会话」契约，spec F7/F10）。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys

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
    """单 server 会话句柄。

    传输上下文由内部 holder task 持有；close 经取消 holder 触发同 task 退出。
    """

    def __init__(self, server: ServerConfig, client_version: str) -> None:
        self.server = server
        self._client_version = client_version
        self._session: ClientSession | None = None
        self._closed = False
        self._holder: asyncio.Task[None] | None = None
        self._stop: asyncio.Event = asyncio.Event()
        self._ready: asyncio.Event = asyncio.Event()
        self._connect_error: BaseException | None = None
        self._tools: list[McpTool] = []

    @property
    def server_name(self) -> str:
        return self.server.name

    async def connect_and_list(self) -> list[McpTool]:
        """打开传输 + 握手 + 列工具；失败抛 MCPStartupError（并让 holder 收尾防泄漏）。"""
        if self._session is not None or self._closed or self._holder is not None:
            raise MCPStartupError(
                f"server {self.server.name} already connected or closed"
            )
        self._holder = asyncio.create_task(
            self._hold(), name=f"mcp-hold-{self.server.name}"
        )
        try:
            await self._ready.wait()
        except asyncio.CancelledError:
            # manager 的 wait_for 超时取消本协程：取消 holder 并等其在自身 task 内收尾
            await self._teardown_holder()
            self._closed = True
            raise
        if self._connect_error is not None:
            # holder 已在自身 task 内退栈；等它结束即可
            await self._teardown_holder()
            self._closed = True
            err = self._connect_error
            raise MCPStartupError(
                f"connect server {self.server.name} failed: {err}"
            ) from err
        return list(self._tools)

    async def _hold(self) -> None:
        """长寿命 task：持有传输/session/http client 上下文，直到被 close 取消。

        上下文经 async with AsyncExitStack 持有；无论正常结束、被取消还是出错，
        栈都在 **本 task 内** 退出，anyio cancel scope 不会跨 task 退出（spec N7）。
        """
        try:
            async with contextlib.AsyncExitStack() as stack:
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
                        await stack.enter_async_context(http_client)
                    transport_ctx = streamable_http_client(
                        self.server.url, http_client=http_client
                    )
                transport = await stack.enter_async_context(transport_ctx)
                read_stream, write_stream = transport  # stdio / http 都 yield 2 元组
                session = await stack.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        client_info=Implementation(
                            name="newcode", version=self._client_version
                        ),
                    )
                )
                await session.initialize()
                listed = await session.list_tools()
                self._session = session
                self._tools = [
                    t
                    for t in (
                        make_tool(self.server.name, self, remote)
                        for remote in listed.tools
                    )
                    if t is not None
                ]
                self._ready.set()
                # 停在此处保持上下文存活，直到 close() 取消本 task
                await self._stop.wait()
        except asyncio.CancelledError:
            # close() 取消本 task：async with 在本 task 内退栈（cancel scope 正确退出）
            raise
        except BaseException as e:  # noqa: BLE001 -- 记录连接错误供 connect_and_list 读取
            self._connect_error = e
            self._ready.set()
            # 栈已在本 task 内自动退出

    async def _teardown_holder(self) -> None:
        """取消 holder 并等其收尾（上下文在 holder 自身 task 内退出）。"""
        if self._holder is None:
            return
        self._stop.set()
        self._holder.cancel()
        try:
            await self._holder
        except (asyncio.CancelledError, Exception):  # noqa: S110,BLE001 -- 收尾路径吞掉 holder 退出异常
            pass
        finally:
            self._holder = None

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
        """取消 holder task 触发上下文在 holder 自身 task 内退出；幂等，不抛。

        MCPManager.close 的单层 5s 兜底已覆盖本方法卡住的情形（spec F11）。
        """
        if self._closed:
            return
        self._closed = True
        await self._teardown_holder()
