"""权限模式枚举与工具类别（L4）"""

from enum import Enum

from .types import Decision


class PermissionMode(Enum):
    """四种权限模式，覆盖规则未命中时的兜底裁决"""

    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS = "bypassPermissions"

    def display_name(self) -> str:
        """返回用户可见名称"""
        names = {
            PermissionMode.DEFAULT: "DEFAULT",
            PermissionMode.ACCEPT_EDITS: "ACCEPT EDITS",
            PermissionMode.PLAN: "PLAN",
            PermissionMode.BYPASS: "BYPASS",
        }
        return names[self]

    @staticmethod
    def parse(s: str) -> "PermissionMode | None":
        """大小写不敏感识别四档名，未知返回 None"""
        lower = s.lower()
        for mode in PermissionMode:
            if mode.value.lower() == lower:
                return mode
        return None


class ToolCategory(Enum):
    """工具类别，用于模式矩阵路由"""

    READONLY = "readonly"  # Read / Glob / Grep，只读且永不触发 Ask
    FILE_WRITE = "file_write"  # Write / Edit
    COMMAND = "command"  # Bash


# 权限模式矩阵：四档 × 三类 → ALLOW/ASK（绝不 Deny）
MODE_MATRIX: dict[PermissionMode, dict[ToolCategory, Decision]] = {
    PermissionMode.DEFAULT: {
        ToolCategory.READONLY: Decision.ALLOW,
        ToolCategory.FILE_WRITE: Decision.ASK,
        ToolCategory.COMMAND: Decision.ASK,
    },
    PermissionMode.ACCEPT_EDITS: {
        ToolCategory.READONLY: Decision.ALLOW,
        ToolCategory.FILE_WRITE: Decision.ALLOW,
        ToolCategory.COMMAND: Decision.ASK,
    },
    PermissionMode.PLAN: {
        ToolCategory.READONLY: Decision.ALLOW,
        ToolCategory.FILE_WRITE: Decision.ASK,
        ToolCategory.COMMAND: Decision.ASK,
    },
    PermissionMode.BYPASS: {
        ToolCategory.READONLY: Decision.ALLOW,
        ToolCategory.FILE_WRITE: Decision.ALLOW,
        ToolCategory.COMMAND: Decision.ALLOW,
    },
}


def resolve_mode(mode: PermissionMode, category: ToolCategory) -> Decision:
    """查表返回 Allow 或 Ask（绝不 Deny）"""
    return MODE_MATRIX[mode][category]
