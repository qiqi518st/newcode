"""TUI /resume 与 /session 命令测试（ch09 T14/T15，spec F8 / AC10-AC11）。

防 bug：STREAMING 时 /resume 误发给 LLM、恢复选择后状态未回 IDLE、
无会话/未启用时崩溃、/session 子命令路由错误。
"""

import asyncio
import json

from rich.console import Console

from mewcode.context.session import _new_session_id
from mewcode.permission.modes import PermissionMode
from mewcode.plans import PlanManager
from mewcode.session.archive import SessionArchive
from mewcode.session.runtime import SessionRuntime
from mewcode.tui.app import REPL, AppMode, SessionState


def make_repl(tmp_path, *, state=SessionState.IDLE, with_runtime=True):
    repl = object.__new__(REPL)
    repl._console = Console(record=True, width=80)
    repl.state = state
    repl.mode = AppMode.NORMAL
    repl._permission_mode = PermissionMode.DEFAULT
    repl.cur_reply = ""
    repl.turn_start = 0.0
    repl._stream_task = None
    repl.plan_manager = PlanManager(str(tmp_path / "plans"))
    repl._pending_plan = ""
    repl._pending_slug = ""
    repl._executing_slug = ""
    repl._session_in_tokens = 0
    repl._session_out_tokens = 0
    repl._current_turn = 0
    repl._retry_count = 0
    if with_runtime:
        repl.session_runtime = SessionRuntime(tmp_path)
        repl.session_archive = SessionArchive(tmp_path)
    else:
        repl.session_runtime = None
        repl.session_archive = None
    return repl


def _make_old_session(workspace, session_id, title, ts):
    d = workspace / ".mewcode" / "sessions" / session_id
    (d / "tool-results").mkdir(parents=True, exist_ok=True)
    with (d / "conversation.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"role": "user", "content": title, "ts": ts, "model": "mock-model"}
            )
            + "\n"
        )


def test_resume_busy_returns_waiting(tmp_path):
    """防 bug：STREAMING 时 /resume 不得进入列表或发送给 LLM。"""
    repl = make_repl(tmp_path, state=SessionState.STREAMING)
    asyncio.run(repl._handle_resume())
    output = repl._console.export_text()
    assert "请等待当前任务完成" in output


def test_resume_not_configured(tmp_path):
    """防 bug：未装配 runtime/archive 时提示未启用而非崩溃。"""
    repl = make_repl(tmp_path, with_runtime=False)
    asyncio.run(repl._handle_resume())
    assert "未启用会话恢复" in repl._console.export_text()


def test_resume_no_sessions(tmp_path):
    """防 bug：无会话时提示并可退出。"""
    repl = make_repl(tmp_path)
    asyncio.run(repl._handle_resume())
    assert "没有可恢复的会话" in repl._console.export_text()


def test_resume_selects_and_switches(tmp_path, monkeypatch):
    """防 bug：选择会话后调用 runtime.resume 且状态回到 IDLE。"""
    sid = _new_session_id()
    _make_old_session(tmp_path, sid, "历史标题", ts=100)
    repl = make_repl(tmp_path)
    calls = []

    async def fake_ask(question, options, default_index=0):
        calls.append(options)
        return sid

    monkeypatch.setattr(repl, "_ask_choice", fake_ask)
    asyncio.run(repl._handle_resume())
    assert repl.session_runtime.session_id == sid
    assert repl.state == SessionState.IDLE
    # 选择列表里包含该会话的 (value, label)
    assert calls and sid in calls[0][0][0]


def test_resume_cancel_keeps_current(tmp_path, monkeypatch):
    """防 bug：Esc 取消后不切换会话。"""
    repl = make_repl(tmp_path)
    before = repl.session_runtime.session_id

    async def fake_ask(question, options, default_index=0):
        return None

    monkeypatch.setattr(repl, "_ask_choice", fake_ask)
    asyncio.run(repl._handle_resume())
    assert repl.session_runtime.session_id == before
    assert repl.state == SessionState.IDLE


def test_session_list_routes(tmp_path):
    """防 bug：/session list 列出历史会话。"""
    sid = _new_session_id()
    _make_old_session(tmp_path, sid, "标题A", ts=100)
    repl = make_repl(tmp_path)
    asyncio.run(repl._handle_session_command("/session list"))
    assert "标题A" in repl._console.export_text()


def test_session_new_creates(tmp_path):
    """防 bug：/session new 创建新会话并替换当前会话。"""
    repl = make_repl(tmp_path)
    old_id = repl.session_runtime.session_id
    asyncio.run(repl._handle_session_command("/session new"))
    new_id = repl.session_runtime.session_id
    assert new_id != old_id
    assert "已创建新会话" in repl._console.export_text()


def test_session_resume_by_id(tmp_path):
    """防 bug：/session resume <id> 等价于选择指定会话。"""
    sid = _new_session_id()
    _make_old_session(tmp_path, sid, "resume-me", ts=100)
    repl = make_repl(tmp_path)
    asyncio.run(repl._handle_session_command(f"/session resume {sid}"))
    assert repl.session_runtime.session_id == sid


def test_session_path_shows_file(tmp_path):
    """防 bug：/session path 定位当前会话文件。"""
    repl = make_repl(tmp_path)
    repl.session_runtime.create_new()
    asyncio.run(repl._handle_session_command("/session path"))
    output = repl._console.export_text()
    assert ".mewcode" in output and "sessions" in output


def test_session_clean_no_active_loss(tmp_path):
    """防 bug：/session clean 只清理过期会话，当前会话保留。"""
    import time

    # 用 40 天前的进程时间生成「过期」目录 ID（clean 按目录 ID 时间判断）
    old_sid = _new_session_id(start_time=time.time() - 40 * 86400)
    _make_old_session(tmp_path, old_sid, "旧会话", ts=100)
    repl = make_repl(tmp_path)
    repl.session_runtime.create_new()  # 当前活动会话
    asyncio.run(repl._handle_session_command("/session clean"))
    assert "已清理 1 个会话" in repl._console.export_text()
    # 旧会话被清理
    assert not (tmp_path / ".mewcode" / "sessions" / old_sid).exists()
    # 当前活动会话保留
    assert repl.session_runtime.context.session_dir is not None


def test_known_command_set_includes_resume_session_memory():
    """防 bug：/resume /session /memory 必须被识别为已知命令。"""
    repl = object.__new__(REPL)
    for cmd in ["/resume", "/session list", "/memory list"]:
        assert repl._is_known_command(cmd), cmd
