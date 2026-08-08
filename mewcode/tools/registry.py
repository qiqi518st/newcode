"""工具注册中心"""

from ..provider.base import ToolDefinition, ToolResult
from .base import Tool


class Registry:
    """集中登记所有可用工具，按名查找，导出 API 格式"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册一个工具"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """按名称查找工具"""
        return self._tools.get(name)

    def to_definitions(self) -> list[ToolDefinition]:
        """导出为 API 所需的工具定义列表"""
        return [
            ToolDefinition(
                name=t.name,
                description=t.description,
                parameters=t.parameters,
            )
            for t in self._tools.values()
        ]

    async def execute(self, name: str, arguments: dict) -> ToolResult:
        """查找并执行工具"""
        tool = self.get(name)
        if tool is None:
            return ToolResult(
                status="error",
                error=f"未知工具: {name}",
            )
        return await tool.execute(arguments)

    @staticmethod
    def default() -> "Registry":
        """预装六个核心工具的注册表"""
        from .file_ops import ReadFileTool, WriteFileTool, EditFileTool
        from .shell import ExecuteCommandTool
        from .search import ListFilesTool, SearchCodeTool

        registry = Registry()
        registry.register(ReadFileTool())
        registry.register(WriteFileTool())
        registry.register(EditFileTool())
        registry.register(ExecuteCommandTool())
        registry.register(ListFilesTool())
        registry.register(SearchCodeTool())
        return registry
