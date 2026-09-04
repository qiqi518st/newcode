"""Tool 协议定义"""

from typing import Protocol

from ..provider.base import ToolResult

# 系统工具名单双保险：除 Tool.is_system 属性外，过滤/豁免时按此名单兜底
# （F3.5：load_skill 等系统级工具不受 allowedTools 过滤约束，恒可见，支持嵌套触发）。
SYSTEM_TOOL_NAMES: frozenset[str] = frozenset({"load_skill"})


class Tool(Protocol):
    """工具协议：每个工具实现名称、描述、参数 Schema、只读属性 和执行方法"""

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

    @property
    def read_only(self) -> bool:
        """是否为只读操作。只读工具可并发执行，有副作用的工具需串行。"""
        ...

    @property
    def is_system(self) -> bool:
        """是否为系统级工具（ch11：load_skill 等）。

        系统工具豁免 allowedTools 过滤（F3.5）、权限提示豁免（N5）；默认 False。
        旧工具未实现此属性时经 getattr 降级为 False（N10 向后兼容）。
        """
        ...

    async def execute(self, arguments: dict) -> ToolResult:
        """执行工具，返回结构化结果"""
        ...


def is_system_tool(tool) -> bool:
    """判断工具是否为系统工具：优先属性，其次名单兜底（F3.5）。"""
    return bool(getattr(tool, "is_system", False)) or tool.name in SYSTEM_TOOL_NAMES
