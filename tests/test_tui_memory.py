"""TUI /memory 命令测试（ch09 T16/T17，spec F13 / AC25-AC27）。

防 bug：/memory 未装配时崩溃、list/show/edit/path/clear 子命令路由错误、
clear 未二次确认误删、show/edit 用错误文件名静默失败、记忆全空时 list 崩溃。
"""

import asyncio

from rich.console import Console

from mewcode.memory.manager import MemoryManager
from mewcode.memory.models import MemoryOperation
from mewcode.permission.modes import PermissionMode
from mewcode.plans import PlanManager
from mewcode.tui.app import REPL, AppMode, SessionState


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
    if with_memory:
        repl.memory_manager = MemoryManager(
            str(tmp_path / ".mewcode" / "memory"),
            str(tmp_path / "user_memory"),
        )
    else:
        repl.memory_manager = None
    return repl


def _seed_note(manager, *, scope="project", filename="pref_short.md", content="keep it short"):
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
    """防 bug：未装配 memory_manager 时提示未启用而非崩溃。"""
    repl = make_repl(tmp_path, with_memory=False)
    asyncio.run(repl._handle_memory_command("/memory list"))
    assert "未启用记忆系统" in repl._console.export_text()


def test_memory_list_empty(tmp_path):
    """防 bug：无记忆时 list 打印提示不崩溃。"""
    repl = make_repl(tmp_path)
    asyncio.run(repl._handle_memory_command("/memory list"))
    assert "暂无记忆" in repl._console.export_text()


def test_memory_list_shows_notes(tmp_path):
    """防 bug：list 按 scope 展示两条记忆。"""
    repl = make_repl(tmp_path)
    _seed_note(repl.memory_manager)
    _seed_note(repl.memory_manager, scope="user", filename="pref_fast.md")
    asyncio.run(repl._handle_memory_command("/memory list"))
    out = repl._console.export_text()
    assert "(project) pref_short.md" in out
    assert "(user) pref_fast.md" in out


def test_memory_show_existing(tmp_path):
    """防 bug：show 打印指定笔记全文。"""
    repl = make_repl(tmp_path)
    _seed_note(repl.memory_manager, content="keep answers short")
    asyncio.run(repl._handle_memory_command("/memory show pref_short.md"))
    assert "keep answers short" in repl._console.export_text()


def test_memory_show_missing(tmp_path):
    """防 bug：show 不存在的文件名提示未找到而非崩溃。"""
    repl = make_repl(tmp_path)
    asyncio.run(repl._handle_memory_command("/memory show nope.md"))
    assert "未找到记忆" in repl._console.export_text()


def test_memory_path(tmp_path):
    """防 bug：path 打印指定笔记的绝对路径。"""
    repl = make_repl(tmp_path)
    _seed_note(repl.memory_manager, scope="user", filename="pref_fast.md")
    asyncio.run(repl._handle_memory_command("/memory path pref_fast.md"))
    out = repl._console.export_text()
    assert "pref_fast.md" in out
    # 路径以正向斜杠打印（Windows 上 str(Path) 是反斜杠，不比字符串）
    assert (
        str(repl.memory_manager.user_store.directory).replace("\\", "/")
        in out.replace("\\", "/")
    )


def test_memory_edit_updates(tmp_path):
    """防 bug：edit 就地更新笔记内容且保留类型/scope。"""
    repl = make_repl(tmp_path)
    _seed_note(repl.memory_manager, content="old")
    asyncio.run(
        repl._handle_memory_command("/memory edit pref_short.md new content here")
    )
    note = repl.memory_manager.project_store.list_notes()[0]
    assert note.content.strip() == "new content here"
    assert note.type == "project_knowledge"


def test_memory_edit_missing(tmp_path):
    """防 bug：edit 不存在的文件名提示未找到而非崩溃。"""
    repl = make_repl(tmp_path)
    asyncio.run(repl._handle_memory_command("/memory edit nope.md x"))
    assert "未找到记忆" in repl._console.export_text()


def test_memory_clear_requires_confirm(tmp_path):
    """防 bug：clear 未确认时不得删除任何记忆（AC25 防误删）。"""
    repl = make_repl(tmp_path)

    async def fake_ask_choice(question, options, default_index=0):
        assert options[0][0] == "yes"  # 确认菜单存在
        return "no"  # 用户取消

    repl._ask_choice = fake_ask_choice
    _seed_note(repl.memory_manager)
    asyncio.run(repl._handle_memory_command("/memory clear"))
    assert "已取消" in repl._console.export_text()
    assert len(repl.memory_manager.list_notes()) == 1  # 未删


def test_memory_clear_confirmed(tmp_path):
    """防 bug：确认后清空全部记忆（project+user）。"""
    repl = make_repl(tmp_path)

    async def fake_ask_choice(question, options, default_index=0):
        return "yes"

    repl._ask_choice = fake_ask_choice
    _seed_note(repl.memory_manager)
    _seed_note(repl.memory_manager, scope="user", filename="pref_fast.md")
    asyncio.run(repl._handle_memory_command("/memory clear"))
    out = repl._console.export_text()
    assert "已清空 2 条记忆" in out
    assert repl.memory_manager.list_notes() == []


def test_memory_bad_subcommand_usage(tmp_path):
    """防 bug：未知子命令打印用法提示而非崩溃。"""
    repl = make_repl(tmp_path)
    asyncio.run(repl._handle_memory_command("/memory wat"))
    assert "用法" in repl._console.export_text()
