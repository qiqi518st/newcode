"""Append-only JSONL session persistence."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Self

from ..provider.base import Message


@dataclass
class Entry:
    role: str | None = None
    content: str | None = None
    tool_calls: list[dict] | None = None
    tool_results: list[dict] | None = None
    tool_call_id: str | None = None
    tool_use_id: str | None = None
    name: str | None = None
    ts: float = field(default_factory=time.time)
    model: str | None = None
    type: str | None = None


def entry_from_message(
    message: Message, model: str | None = None, *, include_model: bool = True
) -> Entry:
    return Entry(
        role=message.role,
        content=message.content,
        tool_calls=message.tool_calls,
        tool_call_id=message.tool_call_id,
        tool_use_id=message.tool_use_id,
        name=message.name,
        model=model if include_model else None,
    )


def message_from_entry(entry: dict[str, Any] | Entry) -> Message | None:
    data = asdict(entry) if isinstance(entry, Entry) else entry
    role = data.get("role")
    if role not in {"user", "assistant", "system", "tool"}:
        return None
    return Message(
        role=role,
        content=data.get("content") or "",
        tool_calls=data.get("tool_calls"),
        tool_call_id=data.get("tool_call_id"),
        tool_use_id=data.get("tool_use_id"),
        name=data.get("name"),
    )


class SessionWriter:
    def __init__(self, session_dir: str | Path, *, model: str | None = None) -> None:
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        (self.session_dir / "tool-results").mkdir(exist_ok=True)
        self.path = self.session_dir / "conversation.jsonl"
        self._fh = self.path.open("a", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()
        self._closed = False
        self.model = model
        self._wrote_model = False

    @classmethod
    def open_existing(
        cls, session_dir: str | Path, *, model: str | None = None
    ) -> SessionWriter:
        return cls(session_dir, model=model)

    def append(self, entry: Entry | dict[str, Any]) -> None:
        data = asdict(entry) if isinstance(entry, Entry) else dict(entry)
        data = {k: v for k, v in data.items() if v is not None}
        if self.model and not self._wrote_model and data.get("role") == "user":
            data["model"] = self.model
            self._wrote_model = True
        with self._lock:
            if self._closed:
                raise RuntimeError("session writer is closed")
            self._fh.write(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def append_message(self, message: Message, *, model: str | None = None) -> None:
        self.append(
            entry_from_message(
                message, model or self.model, include_model=not self._wrote_model
            )
        )

    def append_all(self, messages: list[Message]) -> None:
        for message in messages:
            self.append_message(message)

    def append_event(self, event_type: str, **fields: Any) -> None:
        self.append(Entry(type=event_type, ts=time.time(), **fields))

    def write_compact_marker(self) -> None:
        self.append_event("compact")

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._fh.flush()
                self._fh.close()
                self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
