"""McpTool 适配器：把 SDK 远端 Tool 包装成 newcode 内置 Tool 协议。

- CallerSession Protocol：call_tool 抽象，便于单测注入 stub；生产实现是 MCPConnection。
- make_tool(server_name, caller, remote)：名字拼接 + 禁用字符校验 + 只读性判定。
- McpTool：实现 Tool 协议（name/description/parameters/read_only/execute），execute 只转发。

防的 bug（背景注明）：
- 远端工具名含特殊字符会让 provider 拒收 -> 拼接名须匹配 [A-Za-z0-9_-]+，否则跳过 + 告警（spec F8）。
- inputSchema 为空的工具直接透传会让 provider 拿到空 schema 报错 -> 兜底 {"type":"object"}。
- readOnlyHint 缺失/非法被当成只读放行 -> 安全默认 False（spec F7/N2）。
"""

from __future__ import annotations

import re
import sys
from typing import Any, Protocol

from ..provider.base import ToolResult

# LLM 工具名合法字符集（spec F8）
_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

# 非 text 内容块告警去重：全进程按 full_name 限一次（conn.call_tool 消费）
_non_text_warn_once: set[str] = set()


class CallerSession(Protocol):
    """call_tool 的抽象，便于单测注入 stub；生产实现是 MCPConnection。"""

    async def call_tool(self, name: str, arguments: dict) -> ToolResult: ...


class McpTool:
    """远端 MCP 工具的本地适配器，实现 newcode Tool 协议。"""

    def __init__(
        self,
        caller: CallerSession,
        full_name: str,
        remote_name: str,
        description: str,
        parameters: dict,
        read_only: bool,
    ) -> None:
        self._caller = caller
        self._remote_name = remote_name
        self._full_name = full_name
        self._description = description
        self._parameters = parameters
        self._read_only = read_only

    @property
    def full_name(self) -> str:
        """mcp__<server>__<tool> 完整名（排序/去重键）。"""
        return self._full_name

    @property
    def name(self) -> str:
        return self._full_name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return self._read_only

    async def execute(self, arguments: dict) -> ToolResult:
        """转发到所属连接；结果翻译已在 MCPConnection.call_tool 完成。

        不再 try/except--极端异常由 ToolScheduler 的统一 except 兜底。
        """
        return await self._caller.call_tool(self._remote_name, arguments or {})


def make_tool(server_name: str, caller: CallerSession, remote: Any) -> McpTool | None:
    """把 SDK 远端 Tool 元数据适配为 McpTool；名字含禁用字符 -> None + 告警。

    不依赖具象 MCPConnection（caller: CallerSession Protocol），便于单测注入 stub。
    """
    full_name = f"mcp__{server_name}__{remote.name}"
    if not _VALID_NAME.fullmatch(full_name):
        print(
            f"[mcp] warn: skip tool {full_name}: name contains illegal characters",
            file=sys.stderr,
        )
        return None
    description = remote.description or f"MCP 工具（来自 server {server_name}）"
    # SDK 2.0：Tool.input_schema（snake_case）；空 schema 兜底 {"type":"object"}
    parameters = (
        dict(remote.input_schema)
        if getattr(remote, "input_schema", None)
        else {"type": "object"}
    )
    # SDK 2.0：ToolAnnotations.read_only_hint；annotations 为 None-safe，缺失/非法 -> False
    annotations = getattr(remote, "annotations", None)
    read_only = bool(getattr(annotations, "read_only_hint", None))
    return McpTool(
        caller=caller,
        full_name=full_name,
        remote_name=remote.name,
        description=description,
        parameters=parameters,
        read_only=read_only,
    )
