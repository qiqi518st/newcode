"""MCP 客户端子包：配置加载/合并、SDK 会话封装、工具适配、生命周期管理。

仅依赖 mewcode.tools / mewcode.provider.base（ToolResult）与官方 mcp SDK、标准库；
不依赖 agent / tui / permission / conversation / config 等其它子包。
"""

from .config import ServerConfig, load_mcp_servers
from .conn import MCPConnection, MCPStartupError
from .manager import MCPManager, StartupSummary
from .wrapper import CallerSession, McpTool

__all__ = [
    "CallerSession",
    "MCPConnection",
    "MCPManager",
    "MCPStartupError",
    "McpTool",
    "ServerConfig",
    "StartupSummary",
    "load_mcp_servers",
]
