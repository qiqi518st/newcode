"""恢复段三块（spec F16/F17/F18/F31）：文件快照 + 工具列表 + 边界提示。

三块都是 str，由 Summarizer 拼进「摘要 + 恢复」单条 user 消息的 content。
工具列表直接引用传入的 tool_defs（F17：id(defs) 与 stream 一致，不重算不选子集）。
"""

import json
import time
from dataclasses import dataclass

from ..context.constants import (
    ESTIMATE_CHARS_PER_TOKEN,
    MAX_RECENT_FILES,
    PER_FILE_TOKEN_BUDGET,
)
from ..context.files import FileTracker, TrackedFile
from ..context.skill import SkillRegistry
from ..provider.base import ToolDefinition

BOUNDARY_NOTICE = (
    "[boundary notice]\n"
    "以下是本次压缩前的会话边界提示：\n"
    "- 摘要与恢复信息可能不完整或丢失细节。\n"
    "- 需要文件原文、工具错误原文、用户原话等精确内容时，"
    "请用文件读取工具重新读取对应路径，不要凭摘要或恢复片段猜测全文。\n"
    "- 以重新读取到的真实内容为准。"
)

_MAX_CHARS_PER_FILE = int(PER_FILE_TOKEN_BUDGET * ESTIMATE_CHARS_PER_TOKEN)


@dataclass
class RecoveryBundle:
    """摘要后恢复段三块（每块是一段 str，不各自成 Message）。

    合并成一条 user 消息输出，避免摘要(user)+恢复(user) 连续 user 触发
    Anthropic roles-must-alternate 400（spec F15 合并决策）。
    """

    file_snapshots_text: str  # 最近文件快照（≤5 个，每个 ≤5000 token）
    tools_declaration_text: str  # 工具列表声明（与 stream 同一份 ToolDefinition 引用）
    boundary_notice_text: str  # 边界提示固定文案


class RecoveryBuilder:
    """恢复段三块构造器（工具列表与 stream 同引用，spec F17）。"""

    def __init__(self, skill_registry: SkillRegistry | None = None) -> None:
        self._skill_registry = skill_registry

    async def build(
        self,
        file_tracker: FileTracker,
        tool_defs: list[ToolDefinition],
        skill_registry: SkillRegistry | None = None,
    ) -> RecoveryBundle:
        """构造恢复段三块。

        skill_registry 参数未传时回退到构造时持有的 registry（兼容两种装配方式）。
        """
        registry = skill_registry if skill_registry is not None else self._skill_registry
        recent = await file_tracker.recent(MAX_RECENT_FILES)
        if registry is not None and registry.list():
            # TODO(ch08): Skill 内容加载待后续章节实现——当前 Skill.content 始终为空，
            # 注入分支为空实现。后续按 SKILL_RECOVERY_BUDGET 预算向恢复段追加稳定提示段。
            pass
        return RecoveryBundle(
            file_snapshots_text=_format_snapshots(recent),
            tools_declaration_text=_format_tools(tool_defs),
            boundary_notice_text=BOUNDARY_NOTICE,
        )


def _format_snapshots(recent: list[TrackedFile]) -> str:
    """文件快照文本块：路径 + 读取时间 + 内容片段（每文件 ≤5000 token）。"""
    if not recent:
        return "[files]\n(no recent files)"
    lines = ["[files]"]
    for f in recent:
        age_s = max(0.0, (time.monotonic_ns() - f.timestamp_ns) / 1e9)
        content = f.content
        truncated = len(content) > _MAX_CHARS_PER_FILE
        if truncated:
            content = content[:_MAX_CHARS_PER_FILE]
        lines.append(f"- {f.path} (read {age_s:.1f}s ago)")
        lines.append(content)
        if truncated:
            lines.append("(content truncated)")
    return "\n".join(lines)


def _format_tools(tool_defs: list[ToolDefinition]) -> str:
    """工具列表声明文本（直接用传入引用序列化，spec F17）。"""
    if not tool_defs:
        return "[tools]\n(no tools)"
    lines = ["[tools]"]
    for d in tool_defs:
        lines.append(f"- {d.name}: {d.description}")
        if d.parameters:
            schema = json.dumps(d.parameters, separators=(",", ":"), ensure_ascii=False)
            lines.append(f"    {schema}")
    return "\n".join(lines)
