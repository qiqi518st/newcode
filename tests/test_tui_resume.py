"""TUI /resume 与 /session 命令测试（ch09 T14/T15 迁移到 ch10 新命令面）。

ch10 变更：/resume 改为隐藏命令 → UIController.request_session_list；/session list → /session_list；
/session new → /session_new；/session resume <id> → /session_resume <id>；/session path → /session（路径+id）；
/session clean 按 spec 移除（F8.12/F8.20-F8.22 不含 clean）。

防 bug（保留原意图）：STREAMING 时 /resume 误发给 LLM、恢复选择后状态未回 IDLE、
无会话/未启用时崩溃、命令路由错误。
"""

import asyncio
import json

from rich.console import Console

from mewcode.context.session import _new_session_id
from mewcode.permission.modes import PermissionMode
from mewcode.plans import PlanManager
from mewcode.session.archive import SessionArchive
from mewcode.session.runtime import SessionRuntime
from mewcode.slash import CommandContext, CommandRegistry
from mewcode.slash.commands import register_all
from mewcode.tui.app import REPL, AppMode, RichUIController, SessionState


class _StubAgent:
    """resume/new 重指向所需的 agent 桩。"""

    def __init__(self):
        self.conv = None
        self._context_mgr = None
        self.registry = type("R", (), {"count": lambda self: 3})()
        self.permission = None
        self.provider = None

    def cancel(self):
        pass

    async def run(self, user_input, mode="normal", plan_content=""):
        if False:
            yield None


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
    repl.agent = _StubAgent()
    repl.memory_manager = None
    if with_runtime:
        repl.session_runtime = SessionRuntime(tmp_path)
        repl.session_archive = SessionArchive(tmp_path)
    else:
        repl.session_runtime = None
        repl.session_archive = None
    # 命令系统接线（真实 RichUIController）
    reg = CommandRegistry()
    register_all(reg)
    repl.command_registry = reg
    repl.ui = RichUIController(repl)
    repl.command_ctx = CommandContext(
        registry=reg,
        ui=repl.ui,
        agent=repl.agent,
        conversation=None,
        plan_manager=repl.plan_manager,
        session_runtime=repl.session_runtime,
        session_archive=repl.session_archive,
        memory_manager=repl.memory_manager,
    )
    repl._exit_requested = False
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
    ok = asyncio.run(repl.dispatch_slash("/resume"))
    assert ok is True
    assert "请等待当前任务完成" in repl._console.export_text(clear=False)


def test_resume_not_configured(tmp_path):
    """防 bug：未装配 runtime/archive 时提示未启用而非崩溃。"""
    repl = make_repl(tmp_path, with_runtime=False)
    asyncio.run(repl.dispatch_slash("/resume"))
    assert "未启用会话恢复" in repl._console.export_text(clear=False)


def test_resume_no_sessions(tmp_path):
    """防 bug：无会话时提示并可退出。"""
    repl = make_repl(tmp_path)
    asyncio.run(repl.dispatch_slash("/resume"))
    assert "没有可恢复的会话" in repl._console.export_text(clear=False)


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
    asyncio.run(repl.dispatch_slash("/resume"))
    assert repl.session_runtime.session_id == sid
    assert repl.state == SessionState.IDLE
    # 选择列表里包含该会话的 (value, label)
    assert calls and sid in calls[0][0][0]


def test_resume_cancel_keeps_current(tmp_path, monkeypatch):
    """防 bug：Esc 取消后不切换会话。"""
    sid = _new_session_id()
    _make_old_session(tmp_path, sid, "历史", ts=100)
    repl = make_repl(tmp_path)
    before = repl.session_runtime.session_id

    async def fake_ask(question, options, default_index=0):
        return None

    monkeypatch.setattr(repl, "_ask_choice", fake_ask)
    asyncio.run(repl.dispatch_slash("/resume"))
    assert repl.session_runtime.session_id == before
    assert repl.state == SessionState.IDLE


def test_session_list_routes(tmp_path):
    """防 bug：/session_list 列出历史会话。"""
    sid = _new_session_id()
    _make_old_session(tmp_path, sid, "标题A", ts=100)
    repl = make_repl(tmp_path)
    asyncio.run(repl.dispatch_slash("/session_list"))
    assert "标题A" in repl._console.export_text(clear=False)


def test_session_new_creates(tmp_path):
    """防 bug：/session_new 创建新会话并替换当前会话。"""
    repl = make_repl(tmp_path)
    old_id = repl.session_runtime.session_id
    asyncio.run(repl.dispatch_slash("/session_new"))
    new_id = repl.session_runtime.session_id
    assert new_id != old_id
    assert "已创建新会话" in repl._console.export_text(clear=False)


def test_session_resume_by_id(tmp_path):
    """防 bug：/session_resume <id> 等价于选择指定会话。"""
    sid = _new_session_id()
    _make_old_session(tmp_path, sid, "resume-me", ts=100)
    repl = make_repl(tmp_path)
    asyncio.run(repl.dispatch_slash(f"/session_resume {sid}"))
    assert repl.session_runtime.session_id == sid


def test_session_shows_path_and_id(tmp_path):
    """防 bug：/session 定位当前会话文件与标识。"""
    repl = make_repl(tmp_path)
    repl.session_runtime.create_new()
    asyncio.run(repl.dispatch_slash("/session"))
    output = repl._console.export_text(clear=False)
    assert ".mewcode" in output and "sessions" in output
    assert repl.session_runtime.session_id in output
