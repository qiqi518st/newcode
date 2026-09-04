"""File-based protocol shared by NewCode and the standalone monitor."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from ..provider.base import Message

logger = logging.getLogger(__name__)

_MONITOR_TTL = 5.0


def _monitor_dir(workspace: str | Path) -> Path:
    return Path(workspace) / ".newcode" / "monitor"


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


class MonitorLease:
    """A short-lived heartbeat proving that a monitor is viewing this workspace."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.path = _monitor_dir(workspace) / f"monitor-{os.getpid()}.json"

    def start(self) -> None:
        self.heartbeat()

    def heartbeat(self) -> None:
        _atomic_write(
            self.path,
            {"pid": os.getpid(), "workspace": self.workspace, "heartbeat": time.time()},
        )

    def close(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            logger.debug("failed to remove monitor lease", exc_info=True)


def is_monitor_active(workspace: str) -> bool:
    """Return whether a monitor heartbeat is fresh enough to enable tracing."""
    directory = _monitor_dir(workspace)
    try:
        markers = directory.glob("monitor-*.json")
    except OSError:
        return False
    now = time.time()
    for marker in markers:
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            if now - float(data.get("heartbeat", 0)) <= _MONITOR_TTL:
                return True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return False


def _message_dict(message: Message) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "tool_calls": message.tool_calls,
        "tool_call_id": message.tool_call_id,
        "tool_use_id": message.tool_use_id,
        "name": message.name,
    }


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def write_request_record(
    payload: Any,
    protocol: str,
    model: str,
    request_body: dict[str, Any],
) -> None:
    """Write an exact provider request snapshot when tracing is enabled.

    This function is deliberately best-effort: observability must never change
    whether the provider request is sent.
    """
    trace = getattr(payload, "trace_context", None)
    if not trace:
        return
    path = Path(str(trace["path"]))
    record = {
        **trace,
        "recorded_at": time.time(),
        "protocol": protocol,
        "model": model,
        "assembled_history": [
            _message_dict(message) for message in getattr(payload, "messages", [])
        ],
        "provider_request": _json_value(request_body),
    }
    try:
        _atomic_write(path, record)
    except Exception:
        logger.warning(
            "failed to write provider request trace: %s", path, exc_info=True
        )
