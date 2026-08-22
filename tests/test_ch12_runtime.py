"""ch12 SessionRuntime pending_reminders 生命周期 + 集中重置（spec F5.6/F2.2/N8）。

防的 bug：
- reminder 队列若不清空，/clear 后旧 hook 注入的 prompt 会污染新会话（F5.7 仅本轮有效）。
- 集中重置（reset_for_new_session）若只清 reminders 而不重置 once，
  /clear 后 once hook 不重新触发（AC9 的 /clear 后再次出现）。
- 加锁保证跨线程追加/取出不丢数据（TUI 事件线程与 Agent 循环）。
"""

from __future__ import annotations

import pytest

from mewcode.session.runtime import SessionRuntime

pytestmark = pytest.mark.anyio


class TestReminders:
    def test_initial_empty(self):
        rt = SessionRuntime(".")
        assert rt.take_reminders() == []

    def test_append_and_take(self):
        rt = SessionRuntime(".")
        rt.append_reminders(["a", "b"])
        assert rt.take_reminders() == ["a", "b"]
        assert rt.take_reminders() == []  # 取出即清空

    def test_append_empty_noop(self):
        rt = SessionRuntime(".")
        rt.append_reminders([])
        assert rt.take_reminders() == []

    def test_create_new_clears(self):
        rt = SessionRuntime(".")
        rt.append_reminders(["x"])
        rt.create_new()
        assert rt.take_reminders() == []

    async def test_resume_clears(self, tmp_path):
        rt = SessionRuntime(tmp_path)
        rt.create_new()  # 真实创建会话供 resume
        sid = rt.session_id
        assert sid is not None
        rt.append_reminders(["x"])
        await rt.resume(sid)
        assert rt.take_reminders() == []

    async def test_reset_clears_and_resets_engine(self):
        """reset_for_new_session 清 reminders + 调 hook_engine.reset（N8/AC25）。"""
        rt = SessionRuntime(".")
        rt.append_reminders(["x"])
        calls = {"n": 0}

        class FakeEngine:
            async def reset_for_new_session(self):
                calls["n"] += 1

        rt.hook_engine = FakeEngine()
        await rt.reset_for_new_session()
        assert rt.take_reminders() == []
        assert calls["n"] == 1

    async def test_reset_without_engine_ok(self):
        """hook_engine=None 时 reset 不报错（未启用 Hook 系统）。"""
        rt = SessionRuntime(".")
        rt.append_reminders(["x"])
        await rt.reset_for_new_session()
        assert rt.take_reminders() == []
