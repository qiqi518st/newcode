"""SessionArchive 单测（ch09 T7/T16，spec F8/F10 / AC11/AC17）。

防 bug：旧格式会话混入列表、坏行拖垮整个概要扫描、清理误删当前活动会话、
标题/模型/大小字段缺失、modified_at 无有效记录时退回 mtime。
"""

import json
import os
import time
from pathlib import Path

import pytest

from mewcode.context.session import _new_session_id, is_valid_session_id
from mewcode.session.archive import SessionArchive, clean_expired, list_sessions


def _make_session(workspace: Path, session_id: str, lines: list[dict]) -> Path:
    d = workspace / ".mewcode" / "sessions" / session_id
    (d / "tool-results").mkdir(parents=True, exist_ok=True)
    with (d / "conversation.jsonl").open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    return d


def test_parse_session_time_old_format_rejected():
    """防 bug：旧格式 `<unix_ts>-<random>` 不能通过新格式校验。"""
    assert not is_valid_session_id("1620000000-a1b2c3")
    assert not is_valid_session_id("garbage")
    assert not is_valid_session_id("20260820-120000-xyz!")  # 非法后缀


def test_list_sessions_sorted_by_modified(tmp_path):
    """防 bug：列表按最后有效消息时间倒序，字段完整。"""
    older_id = _new_session_id()
    newer_id = _new_session_id()
    _make_session(
        tmp_path,
        older_id,
        [{"role": "user", "content": "old title", "ts": 1000, "model": "m1"}],
    )
    _make_session(
        tmp_path,
        newer_id,
        [{"role": "user", "content": "new title", "ts": 2000, "model": "m2"}],
    )
    rows = list_sessions(tmp_path)
    assert [s.session_id for s in rows] == [newer_id, older_id]
    first = rows[0]
    assert first.title == "new title"
    assert first.model == "m2"
    assert first.message_count == 1
    assert first.modified_at == 2000
    assert first.file_size > 0


def test_title_truncated_to_50(tmp_path):
    """防 bug：标题超过 50 字符必须截断。"""
    sid = _new_session_id()
    _make_session(tmp_path, sid, [{"role": "user", "content": "X" * 80, "ts": 1}])
    rows = list_sessions(tmp_path)
    assert len(rows[0].title) == 50


def test_bad_lines_recorded_diagnostic(tmp_path):
    """防 bug：坏行不能中断概要扫描，且进入 diagnostics。"""
    sid = _new_session_id()
    d = _make_session(tmp_path, sid, [{"role": "user", "content": "ok", "ts": 1}])
    with (d / "conversation.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("not-json\n")
        fh.write('{"role":"user","content":"later"}\n')
    rows = list_sessions(tmp_path)
    assert rows[0].message_count == 1
    assert any("invalid line" in diag for diag in rows[0].diagnostics)


def test_old_format_not_listed(tmp_path):
    """防 bug：旧格式目录不出现在列表中（只读保留）。"""
    _make_session(
        tmp_path, "1620000000-a1b2c3", [{"role": "user", "content": "legacy", "ts": 1}]
    )
    assert list_sessions(tmp_path) == []


def test_archive_class_delegates(tmp_path):
    """防 bug：SessionArchive 门面类与函数式入口结果一致。"""
    sid = _new_session_id()
    _make_session(tmp_path, sid, [{"role": "user", "content": "t", "ts": 5}])
    archive = SessionArchive(tmp_path)
    assert [s.session_id for s in archive.list()] == [sid]
    assert archive.clean_expired(days=30) == []


def test_cleanup_removes_old_new_format(tmp_path):
    """防 bug：31 天前的目录被清理，文件整体删除含 tool-results。"""
    sid = _new_session_id()
    d = _make_session(tmp_path, sid, [{"role": "user", "content": "old", "ts": 1}])
    (d / "tool-results" / "abc").write_text("x", encoding="utf-8")
    now = time.time() + 40 * 86400  # 40 天后
    removed = clean_expired(tmp_path, days=30, now=now)
    assert removed == [sid]
    assert not d.exists()


def test_cleanup_keeps_recent(tmp_path):
    """防 bug：30 天内的会话不能被清理。"""
    sid = _new_session_id()
    _make_session(tmp_path, sid, [{"role": "user", "content": "fresh", "ts": 1}])
    now = time.time() + 10 * 86400
    assert clean_expired(tmp_path, days=30, now=now) == []
    assert (tmp_path / ".mewcode" / "sessions" / sid).exists()


def test_cleanup_keeps_active_session(tmp_path):
    """防 bug：当前活动 session 即使超龄也不能被清理（AC17）。"""
    sid = _new_session_id()
    _make_session(tmp_path, sid, [{"role": "user", "content": "active", "ts": 1}])
    now = time.time() + 40 * 86400
    assert clean_expired(tmp_path, days=30, now=now, active_session_id=sid) == []


def test_cleanup_keeps_old_format_and_invalid(tmp_path):
    """防 bug：旧格式和无法解析目录保留，不误删。"""
    old = _make_session(
        tmp_path, "1620000000-a1b2c3", [{"role": "user", "content": "legacy", "ts": 1}]
    )
    now = time.time() + 40 * 86400
    assert clean_expired(tmp_path, days=30, now=now) == []
    assert old.exists()


def test_cleanup_single_failure_continues(tmp_path, monkeypatch):
    """防 bug：单个目录删除失败不能中断其他目录的清理。"""
    a = _new_session_id()
    b = _new_session_id()
    _make_session(tmp_path, a, [{"role": "user", "content": "a", "ts": 1}])
    _make_session(tmp_path, b, [{"role": "user", "content": "b", "ts": 1}])
    now = time.time() + 40 * 86400
    real_rmtree = __import__("shutil").rmtree

    def flaky(path, *args, **kwargs):
        if Path(path).name == a:
            raise OSError("permission denied")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("shutil.rmtree", flaky)
    removed = clean_expired(tmp_path, days=30, now=now)
    assert removed == [b]
    assert (tmp_path / ".mewcode" / "sessions" / a).exists()


def test_modified_at_falls_back_to_mtime(tmp_path):
    """防 bug：无有效记录时 modified_at 退回文件 mtime，列表不崩溃。"""
    sid = _new_session_id()
    d = _make_session(tmp_path, sid, [])
    (d / "conversation.jsonl").write_text("", encoding="utf-8")
    rows = list_sessions(tmp_path)
    assert rows[0].message_count == 0
    assert rows[0].modified_at == pytest.approx(
        os.path.getmtime(d / "conversation.jsonl"), abs=2
    )


def test_no_sessions_dir_returns_empty(tmp_path):
    """防 bug：没有 .mewcode/sessions 时返回空列表而非报错。"""
    assert list_sessions(tmp_path) == []
