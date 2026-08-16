"""搜索工具：按 glob 列文件、按正则搜内容"""

import glob
import os
import re

from ..provider.base import ToolResult

_LIST_LIMIT = 100  # list_files 返回上限
_SEARCH_LIMIT = 50  # search_code 返回上限
_SEARCH_SNIPPET = 200  # 每条匹配片段最大字符数


class ListFilesTool:
    """按 glob 模式列出文件路径"""

    @property
    def read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "list_files"

    @property
    def description(self) -> str:
        return (
            "按 glob 模式列出匹配的文件路径，支持 ** 递归匹配。"
            "列出文件优先用本工具而非 shell ls。"
            "找到文件后只报告结果，询问用户是否需要读取内容。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "glob 模式，如 '*.py' 或 '**/*.md'",
                },
                "cwd": {
                    "type": "string",
                    "description": "搜索起始目录（可选，默认当前目录）",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, arguments: dict) -> ToolResult:
        pattern = arguments.get("pattern", "")
        cwd = arguments.get("cwd", os.getcwd())

        try:
            matches = glob.glob(pattern, root_dir=cwd, recursive=True)
            matches = [os.path.join(cwd, m) for m in matches]
            matches = matches[:_LIST_LIMIT]
            output = "\n".join(matches) if matches else "（无匹配文件）"
            return ToolResult(status="ok", output=output)
        except Exception as e:
            return ToolResult(status="error", error=f"列出文件失败: {e}")


class SearchCodeTool:
    """在指定目录下按正则表达式搜索文件内容"""

    @property
    def read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "search_code"

    @property
    def description(self) -> str:
        return (
            "在指定目录下按正则表达式搜索文件内容，返回匹配的文件路径、行号和片段。"
            "搜索代码优先用本工具而非 shell grep；定位后用 read_file 精读。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "正则表达式模式",
                },
                "cwd": {
                    "type": "string",
                    "description": "搜索起始目录（可选，默认当前目录）",
                },
                "glob": {
                    "type": "string",
                    "description": "文件过滤 glob 模式（可选，如 '*.py'）",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, arguments: dict) -> ToolResult:
        pattern = arguments.get("pattern", "")
        cwd = arguments.get("cwd", os.getcwd())
        file_glob = arguments.get("glob", "")

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(status="error", error=f"正则表达式非法: {e}")

        try:
            if file_glob:
                files = glob.glob(file_glob, root_dir=cwd, recursive=True)
                files = [
                    os.path.join(cwd, f)
                    for f in files
                    if os.path.isfile(os.path.join(cwd, f))
                ]
            else:
                files = []
                for root, _dirs, filenames in os.walk(cwd):
                    for fname in filenames:
                        files.append(os.path.join(root, fname))
        except Exception as e:
            return ToolResult(status="error", error=f"遍历目录失败: {e}")

        results = []
        for fpath in files:
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, start=1):
                        if regex.search(line):
                            snippet = line.strip()
                            if len(snippet) > _SEARCH_SNIPPET:
                                snippet = snippet[:_SEARCH_SNIPPET] + "..."
                            rel_path = os.path.relpath(fpath, cwd)
                            results.append(f"{rel_path}:{lineno}: {snippet}")
                            if len(results) >= _SEARCH_LIMIT:
                                break
            except Exception:
                continue
            if len(results) >= _SEARCH_LIMIT:
                break

        output = "\n".join(results) if results else "（无匹配结果）"
        return ToolResult(status="ok", output=output)
