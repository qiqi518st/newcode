"""会话生命周期：会话 id 生成 + 落盘目录管理（spec F33）。"""

import itertools
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SessionContext:
    """会话生命周期信息（进程启动时一次性生成）。"""

    session_id: str  # <unix_ts>-<short_random>，进程内唯一
    spill_dir: str  # 落盘目录 .mewcode/sessions/<sid>/tool-results/


def _new_session_id() -> str:
    """生成 <unix_ts>-<short_random>；secrets 失败时降级 random + warning。"""
    try:
        rand = secrets.token_hex(4)
    except NotImplementedError:  # 极端环境无 os.urandom
        import random

        logger.warning("secrets.token_hex 不可用，降级 random 生成会话 id")
        rand = random.Random(time.time()).randbytes(4).hex()
    return f"{int(time.time())}-{rand}"


def new_session_context(workspace: str) -> SessionContext:
    """构造会话上下文并创建落盘目录（已存在不报错，spec F33）。"""
    session_id = _new_session_id()
    spill_dir = str(
        Path(workspace) / ".mewcode" / "sessions" / session_id / "tool-results"
    )
    Path(spill_dir).mkdir(parents=True, exist_ok=True)
    return SessionContext(session_id=session_id, spill_dir=spill_dir)


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
