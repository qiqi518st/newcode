from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..context.dropper import MessageGroupDropper
from ..context.tokens import estimate_messages
from ..provider.base import Message
from .writer import message_from_entry

logger = logging.getLogger(__name__)


@dataclass
class RecoveryResult:
    messages: list[Message] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    skipped_lines: list[int] = field(default_factory=list)
    truncated: bool = False
    compacted: bool = False
    time_reminder: str | None = None
    last_ts: float | None = None


class SessionRecovery:
    def __init__(self, session_dir: str | Path, **_: object):
        self.session_dir = Path(session_dir)

    def recover(self) -> RecoveryResult:
        return recover_session(self.session_dir)


def _parse_records(result: RecoveryResult, session_dir: str | Path) -> list[Message]:
    """从 JSONL 解析出完整消息前缀：坏行跳过、compact 起点、工具配对截断。"""
    entries: list[tuple[int, dict]] = []
    marker: int | None = None
    path = Path(session_dir) / "conversation.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        result.diagnostics.append(str(exc))
        return []
    for i, line in enumerate(lines, 1):
        try:
            data = json.loads(line)
        except (ValueError, TypeError):
            result.skipped_lines.append(i)
            continue
        if not isinstance(data, dict):
            result.skipped_lines.append(i)
            continue
        if data.get("type") == "compact":
            marker = len(entries)
            continue
        if data.get("role") in {"user", "assistant", "tool", "system"}:
            entries.append((i, data))
    if marker is not None:
        entries = entries[marker:]
    pending: set[str] = set()
    kept: list[Message] = []
    for line_no, data in entries:
        msg = message_from_entry(data)
        if msg is None:
            continue
        if msg.role == "assistant" and msg.tool_calls:
            pending = {str(c.get("id")) for c in msg.tool_calls if c.get("id")}
            kept.append(msg)
        elif msg.role == "tool":
            ident = msg.tool_call_id or msg.tool_use_id
            if ident not in pending:
                result.diagnostics.append(f"orphan tool result line {line_no}")
                continue
            pending.discard(str(ident))
            kept.append(msg)
        else:
            if pending:
                result.truncated = True
                break
            kept.append(msg)
        if isinstance(data.get("ts"), (int, float)):
            result.last_ts = float(data["ts"])
    if pending:
        result.truncated = True
        while kept and kept[-1].role == "tool":
            kept.pop()
        if kept and kept[-1].role == "assistant" and kept[-1].tool_calls:
            kept.pop()
    return kept


def _drop_to_limit(
    messages: list[Message], limit: int | None
) -> tuple[list[Message], bool]:
    """按完整 user 组丢弃最旧内容直到不超限或只剩一组（不拆工具调用对，AC15）。"""
    if limit is None or not messages or estimate_messages(messages) <= limit:
        return messages, False
    groups = MessageGroupDropper.group_by_user(messages)
    truncated = False
    while len(groups) > 1 and estimate_messages(
        [m for g in groups for m in g]
    ) > limit:
        groups = groups[1:]
        truncated = True
    return [m for g in groups for m in g], truncated


def _maybe_time_reminder(
    result: RecoveryResult, now: float | None
) -> None:
    if result.last_ts and (time.time() if now is None else now) - result.last_ts > 6 * 3600:
        hours = int(
            ((time.time() if now is None else now) - result.last_ts) // 3600
        )
        result.time_reminder = (
            f"[系统提示] 本会话已暂停 {hours} 小时。部分上下文可能已过时，"
            "如需最新信息请重新读取相关文件。"
        )
        result.messages.append(Message(role="user", content=result.time_reminder))


def recover_session(
    session_dir: str | Path,
    *,
    now: float | None = None,
    context_window: int | None = None,
    reserve: int = 0,
) -> RecoveryResult:
    """同步恢复：坏行/compact/配对 + 超限按整组降级（无 LLM 压缩路径）。"""
    result = RecoveryResult()
    kept = _parse_records(result, session_dir)
    limit = (context_window - reserve) if context_window else None
    dropped, truncated = _drop_to_limit(kept, limit)
    result.messages = dropped
    result.truncated = result.truncated or truncated
    _maybe_time_reminder(result, now)
    return result


async def recover_session_async(
    session_dir: str | Path,
    *,
    now: float | None = None,
    context_window: int | None = None,
    reserve: int = 0,
    compressor: Callable[[list[Message]], Awaitable[list[Message]]] | None = None,
) -> RecoveryResult:
    """异步恢复：超限先恰好调用一次压缩窄接口，仍超限再整组降级（AC15）。

    compressor 只接收恢复消息副本，不得修改当前会话（plan：不能调用
    会改写会话的 run_force_compact）。
    """
    result = RecoveryResult()
    kept = _parse_records(result, session_dir)
    limit = (context_window - reserve) if context_window else None
    if (
        limit is not None
        and compressor is not None
        and kept
        and estimate_messages(kept) > limit
    ):
        try:
            compressed = await compressor(list(kept))
            if compressed:
                kept = compressed
                result.compacted = True
        except Exception:  # 压缩失败不阻塞恢复（N11 单点失败隔离）
            logger.exception("recovery compaction failed")
            result.diagnostics.append("recovery compaction failed")
    dropped, truncated = _drop_to_limit(kept, limit)
    result.messages = dropped
    result.truncated = result.truncated or truncated
    _maybe_time_reminder(result, now)
    return result
