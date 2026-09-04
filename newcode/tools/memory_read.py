"""记忆读取工具：按文件名读取长期记忆全文

read_file 被路径沙箱锁在项目工作区内，而用户级记忆在 ~/.newcode/memory/ ，
工作区外读不到。read_memory 绕过该沙箱，但仅限读取两个记忆目录中实际存在的
记忆文件（经 MemoryManager.show 匹配 list_notes 的真实文件名，无路径穿越面），
只读、无副作用。

对应 spec F13 加载闭环：索引行携带文件名，Agent 判定相关后用本工具拉取全文。
"""

from ..memory import MemoryManager
from ..provider.base import ToolResult


class ReadMemoryTool:
    """读取一条长期记忆的完整内容"""

    def __init__(self, memory_manager: MemoryManager):
        self._memory_manager = memory_manager

    @property
    def read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "read_memory"

    @property
    def description(self) -> str:
        return (
            "读取一条长期记忆的完整内容。记忆文件位于项目 .newcode/memory/ "
            "与用户 ~/.newcode/memory/ 目录，用文件名（如 user-prefers-any.md）定位。"
            "当系统提示中的长期记忆索引行与当前任务相关时，用本工具获取完整内容。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "记忆文件名，如 user-prefers-any.md",
                },
            },
            "required": ["filename"],
        }

    async def execute(self, arguments: dict) -> ToolResult:
        filename = arguments.get("filename", "")
        if not filename or not isinstance(filename, str):
            return ToolResult(status="error", error="filename 必须是记忆文件名")
        content = self._memory_manager.show(filename)
        if content is None:
            return ToolResult(
                status="error",
                error=f"记忆不存在: {filename}（可用 read_memory 只能读实际存在的记忆文件）",
            )
        return ToolResult(status="ok", output=content)
