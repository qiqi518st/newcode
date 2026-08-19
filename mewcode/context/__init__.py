"""上下文管理子包。

两层压缩（第一层大结果落盘 / 第二层 LLM 摘要）+ Token 估算 + Context Window 解析
+ 文件追踪 + 压缩后恢复 + 自动触发闸 + Skill 骨架 + 会话落盘。

依赖边界：依赖 mewcode.provider.base（Message/ToolCall/ToolResult/TokenUsage/Provider/
ToolDefinition）、mewcode.conversation.manager、mewcode.prompt.assembler（PromptPayload）、
mewcode.llm（PromptTooLongError 哨兵）、mewcode.utils.error 与标准库；
不依赖 agent / tui / permission / mcp / config（context_window 经构造传入
model/protocol 字符串，不读 config）。
"""

from ..context.autogate import AutoCompactGate
from ..context.capabilities import CAPABILITIES
from ..context.constants import (
    AGGREGATE_LIMIT,
    AUTO_GATE_LIMIT,
    AUTO_SAFETY_MARGIN,
    CAPABILITY_TABLE_FLOOR,
    COMPACT_RETRY_LIMIT,
    CONTEXT_WINDOW_FLOOR,
    DEFAULT_WINDOW_ANTHROPIC,
    DEFAULT_WINDOW_OPENAI,
    ESTIMATE_CHARS_PER_TOKEN,
    GROUP_DROP_STEP,
    MANUAL_SAFETY_MARGIN,
    MAX_RECENT_FILES,
    ONE_M_WINDOW,
    PER_FILE_TOKEN_BUDGET,
    PREVIEW_MAX_BYTES,
    PREVIEW_MAX_LINES,
    PTL_DIRECT_RETRY_LIMIT,
    PTL_DROP_RATIO,
    RECENT_COUNT_FLOOR,
    RECENT_TOKEN_FLOOR,
    SINGLE_RESULT_THRESHOLD,
    SKILL_RECOVERY_BUDGET,
    SUMMARY_RESERVE_TOKENS,
)
from ..context.dropper import MessageGroupDropper
from ..context.files import FileTracker, TrackedFile
from ..context.manager import ContextManager
from ..context.recovery import BOUNDARY_NOTICE, RecoveryBuilder, RecoveryBundle
from ..context.replacement import ContentReplacementState
from ..context.session import SessionContext, SessionPaths, new_session_context
from ..context.skill import Skill, SkillRegistry
from ..context.summarize import CompactOutcome, SummarizeConfig, Summarizer
from ..context.tokens import (
    estimate_messages,
    estimate_tokens,
    message_chars,
    usage_to_anchor,
)
from ..context.window import get_context_window_for_model

__all__ = [
    "AGGREGATE_LIMIT",
    "AUTO_GATE_LIMIT",
    "AUTO_SAFETY_MARGIN",
    "BOUNDARY_NOTICE",
    "CAPABILITIES",
    "CAPABILITY_TABLE_FLOOR",
    "COMPACT_RETRY_LIMIT",
    "CONTEXT_WINDOW_FLOOR",
    "DEFAULT_WINDOW_ANTHROPIC",
    "DEFAULT_WINDOW_OPENAI",
    "ESTIMATE_CHARS_PER_TOKEN",
    "GROUP_DROP_STEP",
    "MANUAL_SAFETY_MARGIN",
    "MAX_RECENT_FILES",
    "ONE_M_WINDOW",
    "PER_FILE_TOKEN_BUDGET",
    "PREVIEW_MAX_BYTES",
    "PREVIEW_MAX_LINES",
    "PTL_DIRECT_RETRY_LIMIT",
    "PTL_DROP_RATIO",
    "RECENT_COUNT_FLOOR",
    "RECENT_TOKEN_FLOOR",
    "SINGLE_RESULT_THRESHOLD",
    "SKILL_RECOVERY_BUDGET",
    "SUMMARY_RESERVE_TOKENS",
    "AutoCompactGate",
    "CompactOutcome",
    "ContentReplacementState",
    "ContextManager",
    "FileTracker",
    "MessageGroupDropper",
    "RecoveryBuilder",
    "RecoveryBundle",
    "SessionContext",
    "SessionPaths",
    "Skill",
    "SkillRegistry",
    "SummarizeConfig",
    "Summarizer",
    "TrackedFile",
    "estimate_messages",
    "estimate_tokens",
    "get_context_window_for_model",
    "message_chars",
    "new_session_context",
    "usage_to_anchor",
]
