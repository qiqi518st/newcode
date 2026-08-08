"""Tool 协议定义"""

from typing import Protocol

from ..provider.base import ToolResult


class Tool(Protocol):
    """工具协议：每个工具实现名称、描述、参数 Schema 和执行方法"""

    @property
    def name(self) -> str:
        """工具名称，用于 API 注册和模型识别"""
        ...

    @property
    def description(self) -> str:
        """工具描述，告诉模型这个工具是做什么的"""
        ...

    @property
    def parameters(self) -> dict:
        """参数 JSON Schema（OpenAPI 规范子集）"""
        ...

    async def execute(self, arguments: dict) -> ToolResult:
        """执行工具，返回结构化结果"""
        ...
