"""记忆写入工具：把一条信息写入长期记忆

Agent 收到用户「记住 X」类显式请求时调用本工具，而不是用 write_file/Bash
手动写记忆文件——后者受 L2 沙箱限制（用户级记忆在工作区外）且 Bash 中文
编码会乱码。本工具内部走 MemoryManager.store.apply：位置写死、UTF-8 写入、
原子替换、自动重建 MEMORY.md 索引，只写记忆命名空间，无路径穿越面。

去重：同 scope 下已存在相同 title 的记忆时更新该条，否则新建。
对应 spec F13 记忆写入闭环。
"""

import re

from ..memory import MemoryManager, MemoryOperation
from ..memory.models import TYPE_SCOPE
from ..provider.base import ToolResult

_VALID_TYPE = re.compile(r"^[a-z_]+$")


class WriteMemoryTool:
    """写入一条长期记忆（创建或更新）"""

    def __init__(self, memory_manager: MemoryManager):
        self._memory_manager = memory_manager

    @property
    def read_only(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "write_memory"

    @property
    def description(self) -> str:
        return (
            "写入一条长期记忆。当用户明确要求记住某条信息（如偏好、纠正反馈、"
            "项目知识、参考信息）时使用，不要用 write_file/Bash 手动写记忆文件。"
            "type 取值：user_preference（用户偏好）| correction_feedback（纠正反馈）"
            "| project_knowledge（项目知识）| reference_material（参考信息）。"
            "相同标题的记忆会自动更新而非重复创建。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": [
                        "user_preference",
                        "correction_feedback",
                        "project_knowledge",
                        "reference_material",
                    ],
                    "description": "记忆类型，决定存到用户级(~/.newcode/memory)还是项目级(.newcode/memory)",
                },
                "title": {
                    "type": "string",
                    "description": "记忆标题，简短概括本条内容；同标题记忆会更新",
                },
                "content": {
                    "type": "string",
                    "description": "记忆正文，包含 Why/How to apply 等完整信息",
                },
            },
            "required": ["type", "title", "content"],
        }

    async def execute(self, arguments: dict) -> ToolResult:
        type_ = arguments.get("type")
        title = arguments.get("title")
        content = arguments.get("content")
        if not isinstance(type_, str) or not _VALID_TYPE.fullmatch(type_):
            return ToolResult(status="error", error=f"无效记忆类型: {type_}")
        if type_ not in TYPE_SCOPE:
            return ToolResult(
                status="error",
                error=f"type 必须是 user_preference/correction_feedback/project_knowledge/reference_material，收到: {type_}",
            )
        if not isinstance(title, str) or not title.strip():
            return ToolResult(status="error", error="title 不能为空")
        if not isinstance(content, str) or not content.strip():
            return ToolResult(status="error", error="content 不能为空")

        manager = self._memory_manager
        scope = TYPE_SCOPE[type_]
        store = manager.project_store if scope == "project" else manager.user_store

        # 去重：同 scope 已有相同 title → 更新该条（保留原文件与 created 时间）
        existing = next(
            (n for n in store.list_notes() if n.title == title.strip()), None
        )
        try:
            if existing is not None:
                note = store.apply(
                    MemoryOperation(
                        action="update",
                        level=scope,
                        type=type_,
                        title=title.strip(),
                        filename=existing.filename,
                        content=content.strip(),
                    )
                )
                action_label = "更新"
            else:
                note = store.apply(
                    MemoryOperation(
                        action="create",
                        level=scope,
                        type=type_,
                        title=title.strip(),
                        slug=_slugify(title),
                        content=content.strip(),
                    )
                )
                action_label = "创建"
        except ValueError as exc:
            return ToolResult(status="error", error=f"记忆写入失败: {exc}")

        return ToolResult(
            status="ok",
            output=(
                f"记忆已{action_label}: {note.filename}（{note.scope} 级，"
                f"位于 {'~/.newcode/memory/' if note.scope == 'user' else '.newcode/memory/'}）"
            ),
        )


def _slugify(title: str) -> str:
    """标题 → ASCII 小写短横线 slug。

    MemoryStore._SAFE 只接受 [a-z0-9_-] 文件名（store 的既有约束），中文/其他
    字符一律替换为 -，首字符必须为字母数字，否则 _safe 拒绝写入。
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug or slug[0] in "_-":
        slug = "note-" + slug
    return slug or "note"
