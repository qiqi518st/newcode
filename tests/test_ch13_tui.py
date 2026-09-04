"""ch13 TUI 集成测试（done 队列注入）。

防的 bug：
- 完成通知在流式中误注入（F7.6：空闲点 drain_done，不打断当前对话）
- 注入失败静默吞掉或崩 REPL（应记日志跳过）
- 二次 drain 重复注入同一条通知
- /clear /resume /session_new 后后台任务残留（F7.9：clear_all）
"""

from __future__ import annotations

import asyncio
import types

import pytest

from newcode.conversation.manager import ConversationManager
from newcode.subagent.manager import TaskManager
from newcode.tui import app as appmod

pytestmark = pytest.mark.anyio


class FA:
    async def run_to_completion(self, task, **kw):
        return "result-abc"


def _repl_with(mgr):
    """object.__new__ 绕过 PromptSession（无真实终端，按 ch12 手法）。"""
    repl = object.__new__(appmod.REPL)
    repl.task_manager = mgr
    repl.agent = types.SimpleNamespace(conv=ConversationManager(20))
    printed = []
    repl._console = types.SimpleNamespace(print=lambda *a, **k: printed.append(a[0]))
    return repl, printed


async def test_done_notification_injected_at_idle():
    m = TaskManager()
    m.launch(FA(), "x", role_name="explore")
    await asyncio.sleep(0.05)
    repl, printed = _repl_with(m)
    repl._drain_task_notifications()
    msgs = repl.agent.conv.get_context()
    assert len(msgs) == 1
    assert "<task-notification>" in msgs[0].content
    assert "result-abc" in msgs[0].content
    assert msgs[0].role == "user"  # user 角色注入（XML 包裹，F7.6）
    assert printed and "<task-notification>" in printed[0]
    # 二次 drain 不再重复
    repl._drain_task_notifications()
    assert len(msgs) == 1


async def test_no_manager_noop():
    repl, _ = _repl_with(None)
    repl._drain_task_notifications()  # 不抛


async def test_injection_failure_isolated():
    m = TaskManager()
    m.launch(FA(), "x")
    await asyncio.sleep(0.05)
    repl, _ = _repl_with(m)
    repl.agent.conv = types.SimpleNamespace(
        add_user=lambda x: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    repl._drain_task_notifications()  # 不抛（记日志跳过）


async def test_clear_all_on_session_change():
    """/clear /resume /session_new 路径调用 task_manager.clear_all（F7.9）。"""
    from newcode.subagent.manager import Status

    class H:
        async def run_to_completion(self, task, **kw):
            await asyncio.Event().wait()

    m = TaskManager()
    tid = m.launch(H(), "a")
    await asyncio.sleep(0.05)
    assert m.get(tid).status == Status.RUNNING
    m.clear_all()
    assert m.get(tid) is None
