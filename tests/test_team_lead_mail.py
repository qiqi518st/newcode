"""Lead 邮箱消费与自动续推测试（ch15 F11.3/F11.4/TD-5）。

防的 bug：
- 队员 idle 消息堆积在 Lead 邮箱没人取 → Lead 不知道队员完成（F11.3）
- 队员全 idle、Lead 在 idle 等输入、reminder 静默积累没人取 → 协作卡死（F11.4）
- 自动续推在 STREAMING 态误触发新 Run（F11.4 非 idle 不主动 wake）
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from newcode.session.runtime import SessionRuntime
from newcode.tui.app import REPL, SessionState


class FakeTeamMgr:
    def __init__(self, msgs):
        self.msgs = msgs

    async def poll_lead_mailboxes(self):
        return list(self.msgs)


def _repl(tmp_path):
    r = object.__new__(REPL)
    r.team_mgr = FakeTeamMgr(
        [
            SimpleNamespace(
                team_name="demo",
                from_="alice",
                type="text",
                summary="alice idle",
                content="agent abc finished",
            )
        ]
    )
    r.session_runtime = SessionRuntime(str(tmp_path))
    r._lead_mail_event = asyncio.Event()
    r.state = SessionState.IDLE
    r._session = SimpleNamespace(app=SimpleNamespace(exit=lambda: None))
    r._console = SimpleNamespace(print=lambda *a, **k: None)
    return r


def test_consume_lead_mail_injects_raw_reminder(tmp_path, monkeypatch):
    # 防的 bug：队员 idle 消息不被消费（F11.3）
    async def main():
        repl = _repl(tmp_path)
        import newcode.tui.tasks as tasks_mod

        iterations = {"n": 0}

        async def fast_sleep(_s):
            iterations["n"] += 1
            if iterations["n"] >= 2:
                raise asyncio.CancelledError()

        monkeypatch.setattr(tasks_mod.asyncio, "sleep", fast_sleep)
        task = asyncio.create_task(tasks_mod.consume_lead_mail(repl))
        try:
            await task
        except asyncio.CancelledError:
            pass
        raw = repl.session_runtime.take_raw_reminders()
        assert len(raw) == 1
        assert "<team-update>" in raw[0].content
        assert "alice idle" in raw[0].content
        assert repl._lead_mail_event.is_set()

    asyncio.run(main())


def test_begin_autonomous_turn_runs_stream(tmp_path):
    # 防的 bug：Lead idle 时收到队员更新不自动续推（F11.4 协作卡死）
    async def main():
        repl = _repl(tmp_path)
        calls = []
        repl._run_stream = lambda u, mode, pc: (
            calls.append((u, mode)) or asyncio.sleep(0)
        )
        repl._lead_mail_event.set()
        await repl._begin_autonomous_turn()
        assert calls and "[team-update]" in calls[0][0]
        assert not repl._lead_mail_event.is_set()  # 事件已消费

    asyncio.run(main())


def test_begin_autonomous_turn_skips_when_streaming(tmp_path):
    # 防的 bug：STREAMING 态误触发新 Run（F11.4 非 idle 不主动 wake）
    async def main():
        repl = _repl(tmp_path)
        repl.state = SessionState.STREAMING
        called = []
        repl._run_stream = lambda *a: called.append(a)
        await repl._begin_autonomous_turn()
        assert called == []  # 非 idle 直接返回

    asyncio.run(main())
