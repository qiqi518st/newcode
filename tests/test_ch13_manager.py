"""ch13 subagent/manager.py 后台任务管理器测试。

防的 bug：
- 续派变成新任务（必须同 task_id 复用，round 递增、result 覆盖）——/tasks 查询连贯性
- stop 后不推 done 通知 / 状态不变 cancelled
- 运行中续派不排队而是报错（F7.8：应入队 ≤max_queue_per_agent）
- 任务失败不转 failed 仍 completed（N3：异常兜底）
- done 队列满时 put 卡死（应丢弃 + stderr）
- 前台完成也推通知（F7.6：前台内联完成结果已回主 Agent，不应重复通知）
"""

from __future__ import annotations

import asyncio

import pytest

from mewcode.subagent.manager import (
    Status,
    TaskBusy,
    TaskCapReached,
    TaskManager,
    TaskNotFound,
    build_task_notification,
)


class FakeAgent:
    def __init__(self, texts=None, raise_at=None):
        self.texts = list(texts or ["ok"])
        self.raise_at = raise_at
        self.calls = 0

    async def run_to_completion(self, task, *, already_injected=False, observer=None):
        self.calls += 1
        if self.raise_at is not None and self.calls >= self.raise_at:
            raise RuntimeError("boom")
        return "final:" + task


class HangingAgent:
    async def run_to_completion(self, task, **kw):
        await asyncio.Event().wait()


pytestmark = pytest.mark.anyio


async def test_launch_completed_and_notify():
    m = TaskManager()
    tid = m.launch(FakeAgent(), "t1", name="w1", role_name="explore")
    assert tid.startswith("agent-")
    await asyncio.sleep(0.05)
    bt = m.get(tid)
    assert bt.status == Status.COMPLETED and bt.result == "final:t1"
    assert bt.role == "explore"
    assert tid in m.drain_done()
    assert "<task-notification>" in build_task_notification(bt)


async def test_launch_failure_becomes_failed():
    m = TaskManager()
    tid = m.launch(FakeAgent(raise_at=1), "x")
    await asyncio.sleep(0.05)
    bt = m.get(tid)
    assert bt.status == Status.FAILED and bt.err is not None


async def test_stop_cancels():
    m = TaskManager()
    tid = m.launch(HangingAgent(), "x")
    await asyncio.sleep(0.05)
    assert m.get(tid).status == Status.RUNNING
    m.stop(tid)
    await asyncio.sleep(0.05)
    assert m.get(tid).status == Status.CANCELLED


async def test_continue_same_id_round_increment():
    m = TaskManager(max_tasks_per_agent=3, max_queue_per_agent=1)
    tid = m.launch(FakeAgent(), "r1")
    await asyncio.sleep(0.05)
    assert m.continue_agent(tid, "r2") == tid  # 同 id
    await asyncio.sleep(0.05)
    bt = m.get(tid)
    assert bt.round == 2 and bt.result == "final:r2"


async def test_continue_by_name_latest_wins():
    m = TaskManager()
    m.launch(FakeAgent(), "x", name="w")
    await asyncio.sleep(0.05)
    a2 = m.launch(FakeAgent(), "y", name="w")
    await asyncio.sleep(0.05)
    # 后启动覆盖前弱引用
    assert m.continue_agent("w", "next") == a2
    await asyncio.sleep(0.05)
    assert m.get(a2).round == 2


async def test_running_continue_queues_with_cap():
    m = TaskManager(max_tasks_per_agent=5, max_queue_per_agent=1)
    tid = m.launch(HangingAgent(), "a")
    await asyncio.sleep(0.05)
    assert m.continue_agent(tid, "q1") == tid  # 入队
    with pytest.raises(TaskBusy, match="queue full"):
        m.continue_agent(tid, "q2")


async def test_task_cap_reached():
    m = TaskManager(max_tasks_per_agent=3)
    tid = m.launch(FakeAgent(), "r1")
    await asyncio.sleep(0.05)
    m.continue_agent(tid, "r2")
    await asyncio.sleep(0.05)
    m.continue_agent(tid, "r3")
    await asyncio.sleep(0.05)
    assert m.get(tid).round == 3
    with pytest.raises(TaskCapReached):
        m.continue_agent(tid, "r4")


async def test_not_found():
    with pytest.raises(TaskNotFound):
        TaskManager().continue_agent("nope", "x")


async def test_clear_all():
    m = TaskManager()
    m.launch(HangingAgent(), "a")
    m.launch(FakeAgent(), "b")
    m.clear_all()
    assert m.list() == []


async def test_foreground_completion_no_notify():
    """前台（background=False）完成不推 done 通知——结果已内联回主 Agent（F7.6）。"""
    m = TaskManager()
    handle = m.launch_foreground(FakeAgent(), "x")
    await asyncio.sleep(0.05)
    assert handle.task.status == Status.COMPLETED
    assert m.drain_done() == []


async def test_adopt_marks_without_kill():
    m = TaskManager()
    handle = m.launch_foreground(HangingAgent(), "x")
    await asyncio.sleep(0.05)
    assert m.adopt_running(handle.task_id) is True
    assert m.get(handle.task_id).adopted is True
    assert m.get(handle.task_id).status == Status.RUNNING  # 未杀
