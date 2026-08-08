"""Agent 统一事件流"""

from enum import Enum
from dataclasses import dataclass

from ..provider.base import ToolCall, ToolResult, TokenUsage


class StopReason(Enum):
    """Agent Loop 终止原因"""
    NATURAL = "natural"                          # 自然终止（模型不再要工具）
    MAX_TURNS = "max_turns"                      # 达到迭代上限
    CANCELLED = "cancelled"                       # 用户取消（ESC/Ctrl+C）
    CONSECUTIVE_UNKNOWN_TOOLS = "unknown_tools"   # 连续未知工具
    STREAM_ERROR = "stream_error"                 # Provider 流式错误


@dataclass
class TurnEnd:
    """一轮迭代结束的统计信息"""
    turn: int
    tool_call_count: int
    token_usage: TokenUsage


class EventType(Enum):
    """Agent 事件类型"""
    TEXT = "text"                  # 文本增量
    TOOL_CALL = "tool_call"        # 工具调用请求
    TOOL_RESULT = "tool_result"    # 工具执行结果
    TOKEN_USAGE = "token_usage"    # 每次 LLM API 调用后的 token 用量
    TURN_START = "turn_start"      # 新一轮迭代开始
    TURN_END = "turn_end"          # 一轮迭代结束
    DONE = "done"                  # Agent 运行结束
    ERROR = "error"                # 不可恢复的错误


@dataclass
class Event:
    """Agent 输出事件"""
    type: EventType
    payload: str | ToolCall | ToolResult | TokenUsage | TurnEnd | StopReason | Exception
