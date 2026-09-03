"""团队持久化（ch15 F63/F1.7）：sanitize / 原子写 / 读取 / 跨进程 reload。

- sanitize：只保留 `[a-zA-Z0-9._-]`，其余替换 `-`，首尾去 `-`（F1.4 防路径遍历）
- atomic_write_json：`.tmp` + `os.replace` 原子替换（F63，与 ch12/ch14 一致）
- reload_members_from_disk：跨进程（Pane 后端）reload-before-modify 的核心——
  Lead 与子进程是不同进程、各持一份内存 Team；`add_member`/`set_member_active`
  在加锁后先重读 disk members 再改写再 save，防「子进程内存看不到自己、
  set_member_active 静默 no-op」的丢更新（F19c/F1.7）
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .types import TeammateInfo

# 只保留安全字符集；其他替换为 `-`（F1.4）
_SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]")


def sanitize(name: str) -> str:
    """团队名 → 路径安全名：只保留 [a-zA-Z0-9._-]，其余替换 `-`，首尾去 `-`。

    空结果 / `.` / `..` 抛 ValueError（F1.4 空拒绝 + N6 防路径遍历——
    保留 `.` 的副作用是 `..` 能原样通过，作为目录名会逃逸到父级）。
    """
    cleaned = _SAFE_RE.sub("-", name).strip("-")
    if not cleaned or cleaned in (".", ".."):
        raise ValueError(f"非法团队名: {name!r}（sanitize 后为空或 . / ..）")
    return cleaned


def atomic_write_json(path: str | Path, value: Any) -> None:
    """原子写 JSON（F63）：先写 `<path>.tmp` 再 `os.replace`。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(p)


def read_json(path: str | Path) -> Any:
    """读取 JSON；文件不存在抛 FileNotFoundError。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def reload_members_from_disk(team: object) -> None:
    """调用方持锁；从 config_path 重读 members 覆盖内存（失败静默回退内存现状，F1.7）。

    team 为 team.types.Team；用鸭子类型避免循环导入。
    """
    config_path = getattr(team, "config_path", "")
    if not config_path or not Path(config_path).exists():
        return
    try:
        raw = read_json(config_path)
    except (OSError, ValueError, TypeError):
        return
    if not isinstance(raw, dict):
        return
    members_raw = raw.get("members", [])
    if not isinstance(members_raw, list):
        return
    team.members = [
        TeammateInfo.from_dict(m) for m in members_raw if isinstance(m, dict)
    ]
