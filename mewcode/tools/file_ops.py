"""文件操作工具：读、写、改"""

import os

from ..provider.base import ToolResult
from ..utils.error import PathTraversalError

_READ_LIMIT = 500  # 读文件行数上限


def _check_path(path: str) -> str:
    """路径安全检查：解析为绝对路径并限制在项目工作目录内"""
    abs_path = os.path.abspath(path)
    cwd = os.path.abspath(os.getcwd())
    if not abs_path.startswith(cwd + os.sep) and abs_path != cwd:
        raise PathTraversalError(f"路径超出项目范围: {path}")
    return abs_path


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
            "读取指定路径的文本文件内容，支持可选的行范围切片（offset/limit），大文件自动截断。"
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
                },
                "limit": {
                    "type": "integer",
                    "description": "最大读取行数（可选，默认 500）",
                },
            },
            "required": ["path"],
        }

    async def execute(self, arguments: dict) -> ToolResult:
        path = arguments.get("path", "")
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", _READ_LIMIT)

        try:
            abs_path = _check_path(path)
        except PathTraversalError as e:
            return ToolResult(status="error", error=str(e))

        if not os.path.exists(abs_path):
            return ToolResult(status="error", error=f"文件不存在: {path}")

        if not os.path.isfile(abs_path):
            return ToolResult(status="error", error=f"不是普通文件: {path}")

        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            return ToolResult(status="error", error=f"不是文本文件: {path}")
        except Exception as e:
            return ToolResult(status="error", error=f"读取失败: {e}")

        total = len(lines)
        sliced = lines[offset : offset + limit]
        content = "".join(sliced)
        truncated = len(sliced) < total

        if truncated:
            content += f"\n...（已截断，共 {total} 行，显示 {offset + 1}–{offset + len(sliced)} 行）"

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
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(
                status="ok",
                output=f"已写入 {len(content.encode('utf-8'))} 字节到 {path}",
            )
        except Exception as e:
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
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
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
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as e:
            return ToolResult(status="error", error=f"写入失败: {e}")

        return ToolResult(status="ok", output=f"已替换 1 处（{path}）")
