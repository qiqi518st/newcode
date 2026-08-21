"""工具注册中心"""

from ..permission.modes import ToolCategory
from ..provider.base import ToolDefinition, ToolResult
from .base import Tool

# 友好名 → 内部名映射
FRIENDLY_NAME_MAP: dict[str, str] = {
    "Bash": "execute_command",
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "edit_file",
    "Glob": "list_files",
    "Grep": "search_code",
}

# 内部名 → 友好名反向映射
INTERNAL_TO_FRIENDLY: dict[str, str] = {v: k for k, v in FRIENDLY_NAME_MAP.items()}


class Registry:
    """集中登记所有可用工具，按名查找，导出 API 格式"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册一个工具"""
        self._tools[tool.name] = tool

    def count(self) -> int:
        """已注册工具数量（O(1)，基于内部 dict 长度）。"""
        return len(self._tools)

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

    def read_only_definitions(self) -> list[ToolDefinition]:
        """仅导出只读工具的定义列表（Plan Mode 用）"""
        return [
            ToolDefinition(
                name=t.name,
                description=t.description,
                parameters=t.parameters,
            )
            for t in self._tools.values()
            if t.read_only
        ]

    def is_read_only(self, name: str) -> bool:
        """查询指定工具是否为只读；不存在时返回 False"""
        tool = self.get(name)
        return tool.read_only if tool else False

    def get_friendly_name(self, internal: str) -> str:
        """内部名 → 友好名；未知原样返回"""
        return INTERNAL_TO_FRIENDLY.get(internal, internal)

    def get_category(self, internal: str) -> ToolCategory:
        """工具分类：read_only 优先→READONLY；write_memory→MEMORY；write_file/edit_file→FILE_WRITE；其余→COMMAND"""
        tool = self.get(internal)
        if tool is not None and tool.read_only:
            return ToolCategory.READONLY
        if internal == "write_memory":
            return ToolCategory.MEMORY
        if internal in ("write_file", "edit_file"):
            return ToolCategory.FILE_WRITE
        return ToolCategory.COMMAND

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
        from .file_ops import EditFileTool, ReadFileTool, WriteFileTool
        from .search import ListFilesTool, SearchCodeTool
        from .shell import ExecuteCommandTool

        registry = Registry()
        registry.register(ReadFileTool())
        registry.register(WriteFileTool())
        registry.register(EditFileTool())
        registry.register(ExecuteCommandTool())
        registry.register(ListFilesTool())
        registry.register(SearchCodeTool())
        return registry
