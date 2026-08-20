from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..context.session import is_valid_session_id, parse_session_time

logger = logging.getLogger(__name__)


@dataclass
class SessionSummary:
    session_id: str
    path: Path
    title: str = ""
    model: str | None = None
    message_count: int = 0
    modified_at: float = 0
    file_size: int = 0
    diagnostics: list[str] = field(default_factory=list)


class SessionArchive:
    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / ".mewcode" / "sessions"

    def list(self) -> list[SessionSummary]:
        return list_sessions(self.workspace)

    def clean_expired(
        self, days: int = 30, active_session_id: str | None = None
    ) -> list[str]:
        return clean_expired(self.workspace, days, active_session_id=active_session_id)


def _scan(path: Path) -> SessionSummary:
    summary = SessionSummary(path.name, path)
    convo = path / "conversation.jsonl"
    summary.file_size = convo.stat().st_size if convo.exists() else 0
    try:
        for line_no, raw in enumerate(
            convo.read_text(encoding="utf-8").splitlines(), 1
        ):
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                summary.diagnostics.append(f"invalid line {line_no}")
                continue
            if (
                not isinstance(data, dict)
                or not data.get("role")
                or not isinstance(data.get("ts"), (int, float))
            ):
                continue
            summary.message_count += 1
            summary.modified_at = max(summary.modified_at, float(data["ts"]))
            if data.get("model") and summary.model is None:
                summary.model = str(data["model"])
            if not summary.title and data.get("role") == "user":
                summary.title = str(data.get("content") or "")[:50]
    except OSError as exc:
        summary.diagnostics.append(str(exc))
    if not summary.modified_at:
        try:
            summary.modified_at = convo.stat().st_mtime
        except OSError:
            pass
    return summary


def list_sessions(workspace: str | Path) -> list[SessionSummary]:
    root = Path(workspace).resolve() / ".mewcode" / "sessions"
    if not root.is_dir():
        return []
    rows = [
        _scan(p)
        for p in root.iterdir()
        if p.is_dir()
        and is_valid_session_id(p.name)
        and (p / "conversation.jsonl").is_file()
    ]
    return sorted(rows, key=lambda s: s.modified_at, reverse=True)


def clean_expired(
    workspace: str | Path,
    days: int = 30,
    *,
    active_session_id: str | None = None,
    now: float | None = None,
) -> list[str]:
    root = Path(workspace).resolve() / ".mewcode" / "sessions"
    removed: list[str] = []
    cutoff = (time.time() if now is None else now) - days * 86400
    if not root.is_dir():
        return removed
    for p in root.iterdir():
        if (
            not p.is_dir()
            or p.name == active_session_id
            or not is_valid_session_id(p.name)
        ):
            continue
        stamp = parse_session_time(p.name)
        if stamp is None or stamp.timestamp() >= cutoff:
            continue
        try:
            shutil.rmtree(p)
            removed.append(p.name)
        except OSError as exc:
            logger.warning("session cleanup failed for %s: %s", p.name, exc)
    return removed
