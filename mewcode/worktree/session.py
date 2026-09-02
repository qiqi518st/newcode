"""WorktreeSession 持久化（ch14 F10）：JSON 序列化 + 原子读写。

- WorktreeSession：当前活跃 worktree 会话（F2.2），含 to_json/from_json
- save_session：None 写 "null"（F10.2 退出不删文件）；原子写 tmp + os.replace（F10.1）
- load_session：缺失/空/null → None；非法 JSON → stderr 警告清空 → None（N5 不阻断启动）
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class WorktreeSession:
    """当前活跃 worktree 会话（spec F2.2）。"""

    original_cwd: str
    worktree_path: str
    worktree_name: str
    original_branch: str
    original_head_commit: str
    session_id: str  # uuid / secrets hex
    hook_based: bool = False  # 预留

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> WorktreeSession:
        return cls(**json.loads(raw))


def save_session(session_file: Path, session: WorktreeSession | None) -> None:
    """原子写（F10.1）：先写 <file>.tmp 再 os.replace；session=None 写 "null"。"""
    session_file.parent.mkdir(parents=True, exist_ok=True)
    content = "null" if session is None else session.to_json()
    tmp = session_file.with_suffix(session_file.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, session_file)


def load_session(session_file: Path) -> WorktreeSession | None:
    """读取 session；缺失/空/null → None；非法 JSON → 警告清空 → None（N5）。"""
    if not session_file.exists():
        return None
    try:
        raw = session_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw or raw == "null":
        return None
    try:
        return WorktreeSession.from_json(raw)
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        print(
            f"worktree: session 文件损坏，已清空: {session_file} ({exc})",
            file=sys.stderr,
        )
        _write_null(session_file)
        return None


def clear_session(session_file: Path) -> None:
    """清空 session（= save_session(None)，F10.2）。"""
    save_session(session_file, None)


def _write_null(session_file: Path) -> None:
    try:
        session_file.write_text("null", encoding="utf-8")
    except OSError:
        pass
