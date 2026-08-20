"""会话生命周期：会话 id 生成 + 落盘目录管理（spec F33）。"""

import itertools
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SessionContext:
    """会话生命周期信息（进程启动时一次性生成）。"""

    session_id: str
    spill_dir: str
    session_dir: str | None = None
    conversation_path: str | None = None

    def __post_init__(self) -> None:
        if self.session_dir is None:
            self.session_dir = str(Path(self.spill_dir).parent)
        if self.conversation_path is None:
            self.conversation_path = str(Path(self.session_dir) / "conversation.jsonl")


_SESSION_RE = re.compile(r"^(\d{8})-(\d{6})-([0-9a-fA-F]{4})$")
_PROCESS_START = time.time()
_LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc


def _new_session_id(start_time: float | None = None) -> str:
    """Generate the ch09 session id, using one process-start timestamp."""
    try:
        rand = secrets.token_hex(2)
    except NotImplementedError:  # 极端环境无 os.urandom
        import random

        logger.warning("secrets.token_hex 不可用，降级 random 生成会话 id")
        rand = random.Random(time.time()).randbytes(4).hex()
    stamp = datetime.fromtimestamp(
        _PROCESS_START if start_time is None else start_time, _LOCAL_TZ
    )
    return f"{stamp:%Y%m%d-%H%M%S}-{rand[:4]}"


def parse_session_time(session_id: str) -> datetime | None:
    match = _SESSION_RE.fullmatch(session_id)
    if not match:
        return None
    try:
        return datetime.strptime(
            f"{match.group(1)}-{match.group(2)}", "%Y%m%d-%H%M%S"
        ).replace(tzinfo=_LOCAL_TZ)
    except ValueError:
        return None


def is_valid_session_id(session_id: str) -> bool:
    return parse_session_time(session_id) is not None


def new_session_context(workspace: str) -> SessionContext:
    """构造会话上下文并创建落盘目录（已存在不报错，spec F33）。"""
    sessions_root = Path(workspace) / ".mewcode" / "sessions"
    # Random suffix collisions are unlikely, but the directory is the authoritative
    # uniqueness check and also handles deterministic test doubles.
    for _ in range(32):
        session_id = _new_session_id()
        session_dir = sessions_root / session_id
        try:
            session_dir.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            continue
    else:
        raise RuntimeError("unable to allocate a unique session id")
    spill_dir = session_dir / "tool-results"
    spill_dir.mkdir(exist_ok=True)
    conversation_path = session_dir / "conversation.jsonl"
    conversation_path.touch(exist_ok=True)
    return SessionContext(
        session_id, str(spill_dir), str(session_dir), str(conversation_path)
    )


def open_session_context(workspace: str, session_id: str) -> SessionContext:
    if not is_valid_session_id(session_id) or Path(session_id).name != session_id:
        raise ValueError("invalid session id")
    root = (Path(workspace) / ".mewcode" / "sessions").resolve()
    session_dir = (root / session_id).resolve()
    if not session_dir.is_relative_to(root):
        raise ValueError("session path escapes workspace")
    if not session_dir.is_dir():
        raise FileNotFoundError(session_dir)
    spill = session_dir / "tool-results"
    spill.mkdir(exist_ok=True)
    return SessionContext(
        session_id,
        str(spill),
        str(session_dir),
        str(session_dir / "conversation.jsonl"),
    )


class SessionPaths:
    """落盘路径工具：按 tool_use_id 定位文件；空 id 用自增序号兜底。"""

    def __init__(self, session: SessionContext) -> None:
        self._spill_dir = Path(session.spill_dir)
        self._session_id = session.session_id
        self._session_dir = self._spill_dir.parent
        self._fallback = itertools.count(1)
        self._request_counter = itertools.count(1)

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    @property
    def spill_dir(self) -> Path:
        return self._spill_dir

    def request_trace_path(self) -> Path:
        """Return a unique request record path for the current session."""
        trace_dir = self._session_dir / "requests"
        trace_dir.mkdir(parents=True, exist_ok=True)
        return trace_dir / f"request-{next(self._request_counter):06d}.json"

    def path_for(self, tool_use_id: str) -> Path:
        """返回落盘路径；空 id 兜底为 unknown-{n}（不抛，spec F3）。"""
        if tool_use_id:
            return self._spill_dir / tool_use_id
        return self._spill_dir / f"unknown-{next(self._fallback)}"

    def ensure_dir(self) -> None:
        """确保落盘目录存在（幂等）。"""
        self._spill_dir.mkdir(parents=True, exist_ok=True)
