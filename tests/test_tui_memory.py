"""TUI /memory 命令测试（ch09 T16/T17 迁移到 ch10 新命令面）。

ch10 变更：/memory show/edit/path 子命令按 spec 移除（F8.10 只读列文件名），
清空改走 /memory_clear（F8.16，不要求二次确认）；详情走 /memory_list；新增 /memory_add。

防 bug（保留原意图）：未装配时崩溃、记忆全空时崩溃、新增/清空路由错误。
"""

import asyncio

from rich.console import Console

from newcode.memory.manager import MemoryManager
from newcode.memory.models import MemoryOperation
from newcode.permission.modes import PermissionMode
from newcode.plans import PlanManager
from newcode.slash import CommandContext, CommandRegistry
from newcode.slash.commands import register_all
from newcode.tui.app import REPL, AppMode, RichUIController, SessionState


class _StubAgent:
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


def make_repl(tmp_path, *, with_memory=True):
    repl = object.__new__(REPL)
    repl._console = Console(record=True, width=120)
    repl.state = SessionState.IDLE
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
    repl.session_runtime = None
    repl.session_archive = None
    repl.agent = _StubAgent()
    if with_memory:
        repl.memory_manager = MemoryManager(
            str(tmp_path / ".newcode" / "memory"),
            str(tmp_path / "user_memory"),
        )
    else:
        repl.memory_manager = None
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
        session_runtime=None,
        session_archive=None,
        memory_manager=repl.memory_manager,
    )
    repl._exit_requested = False
    return repl


def _seed_note(
    manager, *, scope="project", filename="pref_short.md", content="keep it short"
):
    """直接写入一条记忆笔记（绕过 LLM 更新路径）。"""
    store = manager.project_store if scope == "project" else manager.user_store
    store.apply(
        MemoryOperation(
            action="create",
            level=scope,
            type="project_knowledge" if scope == "project" else "user_preference",
            filename=filename,
            content=content,
        )
    )


def test_memory_not_configured(tmp_path):
    """防 bug：未装配 memory_manager 时提示而非崩溃。"""
    repl = make_repl(tmp_path, with_memory=False)
    asyncio.run(repl.dispatch_slash("/memory_list"))
    assert "未启用记忆系统" in repl._console.export_text(clear=False)


def test_memory_list_empty(tmp_path):
    """防 bug：无记忆时 /memory 打印提示不崩溃。"""
    repl = make_repl(tmp_path)
    asyncio.run(repl.dispatch_slash("/memory"))
    assert "无已加载的记忆文件" in repl._console.export_text(clear=False)


def test_memory_lists_filenames(tmp_path):
    """防 bug：/memory 只列文件名清单（F8.10）。"""
    repl = make_repl(tmp_path)
    _seed_note(repl.memory_manager, filename="pref_short.md")
    _seed_note(repl.memory_manager, scope="user", filename="pref_fast.md")
    asyncio.run(repl.dispatch_slash("/memory"))
    out = repl._console.export_text(clear=False)
    assert "pref_short.md" in out
    assert "pref_fast.md" in out


def test_memory_list_shows_details(tmp_path):
    """防 bug：/memory_list 按 scope 展示详情。"""
    repl = make_repl(tmp_path)
    _seed_note(repl.memory_manager)
    _seed_note(repl.memory_manager, scope="user", filename="pref_fast.md")
    asyncio.run(repl.dispatch_slash("/memory_list"))
    out = repl._console.export_text(clear=False)
    assert "(project) pref_short.md" in out
    assert "(user) pref_fast.md" in out


def test_memory_add_then_list(tmp_path):
    """防 bug：/memory_add 后 /memory_list 可见（F8.15/AC16）。"""
    repl = make_repl(tmp_path)
    asyncio.run(repl.dispatch_slash("/memory_add user_preference 记住 tea"))
    asyncio.run(repl.dispatch_slash("/memory_list"))
    out = repl._console.export_text(clear=False)
    assert "user_preference" in out
    assert len(repl.memory_manager.list_notes()) == 1


def test_memory_add_bad_type(tmp_path):
    """防 bug：/memory_add 未知类型提示而非崩溃。"""
    repl = make_repl(tmp_path)
    asyncio.run(repl.dispatch_slash("/memory_add wat 内容"))
    assert "未知记忆类型" in repl._console.export_text(clear=False)


def test_memory_clear_clears(tmp_path):
    """防 bug：/memory_clear 清空全部记忆（F8.16/AC16）。"""
    repl = make_repl(tmp_path)
    _seed_note(repl.memory_manager)
    _seed_note(repl.memory_manager, scope="user", filename="pref_fast.md")
    asyncio.run(repl.dispatch_slash("/memory_clear"))
    out = repl._console.export_text(clear=False)
    assert "已清空 2 条记忆" in out
    assert repl.memory_manager.list_notes() == []
