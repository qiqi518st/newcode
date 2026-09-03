"""邮箱消息类型（ch15 F32/F9）：Message / MessageType。

三种结构化消息（F9）：纯文本（必带 summary）、shutdown_request/response（优雅退出协商）、
plan_approval_response（Plan 审批回复，仅 Lead 可发）；同一 SendMessage 入口以 type 分流。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """消息类型（F9）。str,Enum 兼容 Python 3.10。"""

    TEXT = "text"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    PLAN_APPROVAL_RESPONSE = "plan_approval_response"

    def __str__(self) -> str:
        return self.value


@dataclass
class Message:
    """一条邮箱消息（F4.3/F32）：落盘自动补 timestamp、默认未读。"""

    from_: str  # 发件人
    to: str  # 收件人
    type: MessageType = MessageType.TEXT
    summary: str = ""  # 纯文本消息必带 5-10 词摘要
    content: str = ""  # 正文
    payload: dict[str, Any] | None = (
        None  # 结构化消息载荷（如 plan_approval {approve, feedback}）
    )
    timestamp: int = 0
    read: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_,
            "to": self.to,
            "type": self.type.value,
            "summary": self.summary,
            "content": self.content,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "read": self.read,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Message:
        return cls(
            from_=str(raw.get("from", "")),
            to=str(raw.get("to", "")),
            type=MessageType(raw.get("type", MessageType.TEXT.value)),
            summary=str(raw.get("summary", "")),
            content=str(raw.get("content", "")),
            payload=raw.get("payload"),
            timestamp=int(raw.get("timestamp", 0) or 0),
            read=bool(raw.get("read", False)),
        )
