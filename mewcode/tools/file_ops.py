"""文件操作工具：读、写、改"""

import os

from ..permission.sandbox import check_path as sandbox_check
from ..provider.base import ToolResult
from ..utils.error import PathTraversalError

_DEFAULT_READ_LIMIT = 500  # 未传 limit 时的默认读取行数


def _check_path(path: str) -> str:
    """路径安全检查：解析为绝对路径并限制在项目工作目录内

    使用沙箱模块做符号链接感知的路径检查（双重保险）。
    """
    abs_path = os.path.abspath(path)
    cwd = os.path.abspath(os.getcwd())

    # 沙箱检查（符号链接感知）
    ok, resolved = sandbox_check(path, cwd)
    if not ok:
        raise PathTraversalError(f"路径超出项目范围: {path}")

    # 返回 resolved 路径（已解析符号链接/祖先）
    return resolved if resolved else abs_path


class ReadFileTool:
    """读取文件内容，支持行范围切片"""

    @property
    def read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "读取指定路径的文本文件内容，支持可选的行范围切片（offset/limit）。"
            "查看文件内容优先用本工具而非 shell cat；修改文件前必须先读取目标内容（先读后改）。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径，支持相对路径或绝对路径",
                },
                "offset": {
                    "type": "integer",
                    "description": "起始行号（从 0 开始，可选）",
                    "minimum": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "最大读取行数（可选，不设置时默认 500；无固定上限）",
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }

    async def execute(self, arguments: dict) -> ToolResult:
        path = arguments.get("path", "")
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", _DEFAULT_READ_LIMIT)

        if not isinstance(offset, int) or not isinstance(limit, int):
            return ToolResult(status="error", error="offset 和 limit 必须是整数")
        if offset < 0 or limit <= 0:
            return ToolResult(status="error", error="offset 必须非负，limit 必须为正数")

        try:
            abs_path = _check_path(path)
        except PathTraversalError as e:
            return ToolResult(status="error", error=str(e))

        if not os.path.exists(abs_path):
            return ToolResult(status="error", error=f"文件不存在: {path}")

        if not os.path.isfile(abs_path):
            return ToolResult(status="error", error=f"不是普通文件: {path}")

        try:
            with open(abs_path, "r", encoding="utf-8") as f:  # noqa: ASYNC230
                lines = f.readlines()
        except UnicodeDecodeError:
            return ToolResult(status="error", error=f"不是文本文件: {path}")
        except Exception as e:  # noqa: BLE001 - convert tool failures to ToolResult
            return ToolResult(status="error", error=f"读取失败: {e}")

        total = len(lines)
        sliced = lines[offset : offset + limit]
        content = "".join(sliced)
        truncated = len(sliced) < total

        if truncated:
            content += "\n...（本次调用未返回全部文件）"

        return ToolResult(status="ok", output=content, truncated=truncated)


class WriteFileTool:
    """写入文件内容，目录不存在自动创建"""

    @property
    def read_only(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "向指定路径写入文本内容，目录不存在自动创建，文件已存在则覆盖。"
            "新建或整体覆盖文件时用本工具；写入前建议先 read_file 查看现有内容。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的文本内容",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, arguments: dict) -> ToolResult:
        path = arguments.get("path", "")
        content = arguments.get("content", "")

        try:
            abs_path = _check_path(path)
        except PathTraversalError as e:
            return ToolResult(status="error", error=str(e))

        try:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:  # noqa: ASYNC230
                f.write(content)
            return ToolResult(
                status="ok",
                output=f"已写入 {len(content.encode('utf-8'))} 字节到 {path}",
            )
        except Exception as e:  # noqa: BLE001 - convert tool failures to ToolResult
            return ToolResult(status="error", error=f"写入失败: {e}")


class EditFileTool:
    """原文唯一匹配替换"""

    @property
    def read_only(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "在指定文件中进行原文唯一匹配替换，old_string 必须在文件中恰好出现一次。"
            "修改前必须先 read_file 读取目标内容（先读后改）；old_string 需与文件内容逐字一致。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径",
                },
                "old_string": {
                    "type": "string",
                    "description": "要替换的原始字符串，必须在文件中恰好出现一次",
                },
                "new_string": {
                    "type": "string",
                    "description": "替换后的新字符串",
                },
            },
            "required": ["path", "old_string", "new_string"],
        }

    async def execute(self, arguments: dict) -> ToolResult:
        path = arguments.get("path", "")
        old_string = arguments.get("old_string", "")
        new_string = arguments.get("new_string", "")

        try:
            abs_path = _check_path(path)
        except PathTraversalError as e:
            return ToolResult(status="error", error=str(e))

        if not os.path.exists(abs_path):
            return ToolResult(status="error", error=f"文件不存在: {path}")

        try:
            with open(abs_path, "r", encoding="utf-8") as f:  # noqa: ASYNC230
                content = f.read()
        except Exception as e:  # noqa: BLE001 - convert tool failures to ToolResult
            return ToolResult(status="error", error=f"读取失败: {e}")

        count = content.count(old_string)
        if count == 0:
            return ToolResult(status="error", error="old_string 在文件中未找到")
        if count > 1:
            return ToolResult(
                status="error",
                error=f"old_string 在文件中找到 {count} 处，无法确定替换哪一处",
            )

        new_content = content.replace(old_string, new_string, 1)

        try:
            with open(abs_path, "w", encoding="utf-8") as f:  # noqa: ASYNC230
                f.write(new_content)
        except Exception as e:  # noqa: BLE001 - convert tool failures to ToolResult
            return ToolResult(status="error", error=f"写入失败: {e}")

        return ToolResult(status="ok", output=f"已替换 1 处（{path}）")
