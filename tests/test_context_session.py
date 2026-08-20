"""会话落盘路径单测（ch08 T27，spec F33）。

防 bug：会话 id 格式错乱、空 id 兜底抛异常、落盘目录未创建。
"""

import re
from pathlib import Path

from mewcode.context.session import SessionPaths, new_session_context


def test_session_id_format(tmp_path):
    """防 bug：ch09 会话 id 必须使用本地启动时间和四位十六进制后缀。"""
    sc = new_session_context(str(tmp_path))
    assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{4}$", sc.session_id), sc.session_id
    date_part, time_part, _ = sc.session_id.split("-")
    assert len(date_part) == 8 and len(time_part) == 6

    sc2 = new_session_context(str(tmp_path))
    assert sc.session_id != sc2.session_id, "会话 id 必须唯一"


def test_session_id_collision_retries(tmp_path, monkeypatch):
    """防 bug：同秒/固定随机后缀碰撞时不能复用已有会话目录。"""
    import mewcode.context.session as session_module

    ids = iter(["20260820-120000-abcd", "20260820-120000-ef01"])
    monkeypatch.setattr(session_module, "_new_session_id", lambda: next(ids))
    first = new_session_context(str(tmp_path))
    second = new_session_context(str(tmp_path))
    assert first.session_id == "20260820-120000-abcd"
    assert second.session_id == "20260820-120000-ef01"


def test_spill_dir_created(tmp_path):
    """防 bug：new_session_context 未创建落盘目录 → 后续落盘 FileNotFoundError。"""
    sc = new_session_context(str(tmp_path))
    assert Path(sc.spill_dir).exists() and Path(sc.spill_dir).is_dir()


def test_path_for(tmp_path):
    """防 bug：path_for 返回路径不在 spill_dir/<id> 下 → 文件散落。"""
    sc = new_session_context(str(tmp_path))
    sp = SessionPaths(sc)
    p = sp.path_for("tool_123")
    assert p == Path(sc.spill_dir) / "tool_123"


def test_path_for_empty_fallback(tmp_path):
    """防 bug：空 tool_use_id 时抛异常或路径冲突 → 落盘失败。

    空 id 兜底为 unknown-{n}，不抛且递增。
    """
    sc = new_session_context(str(tmp_path))
    sp = SessionPaths(sc)
    p1 = sp.path_for("")
    p2 = sp.path_for("")
    assert p1 != p2, "空 id 兜底名应递增"
    assert p1.name.startswith("unknown-1")
    assert p2.name.startswith("unknown-2")


def test_ensure_dir_idempotent(tmp_path):
    """防 bug：ensure_dir 对已存在目录报错 → 幂等性破坏。"""
    sc = new_session_context(str(tmp_path))
    sp = SessionPaths(sc)
    sp.ensure_dir()  # 已存在，不应抛
    sp.ensure_dir()  # 再调一次，幂等
    assert Path(sc.spill_dir).exists()
