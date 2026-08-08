"""Agent 统一事件流"""

from enum import Enum
from dataclasses import dataclass

from ..provider.base import ToolCall, ToolResult


class EventType(Enum):
    """Agent 事件类型"""
    TEXT = "text"              # 文本增量
    TOOL_CALL = "tool_call"    # 工具调用请求
    TOOL_RESULT = "tool_result" # 工具执行结果
    DONE = "done"              # 本轮结束
    ERROR = "error"            # 出错


@dataclass
class Event:
    """Agent 输出事件"""
    type: EventType
    payload: str | ToolCall | ToolResult | Exception
