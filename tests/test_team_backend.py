"""执行后端测试（ch15 F2-F5/F11-F18）：detect 四分支 + tmux 命令构造 + in-process。

防的 bug：
- detect 优先级错乱（$TMUX 内优先、iTerm.app+it2、PATH 有 tmux、回落 in-process，F2.4）
- tmux spawn 命令缺 --agent-id（子进程读不到自己的 agent_id，F3.2）
- initial_prompt 泄漏进命令行（shell-quote 边界，F2.6）
- 会话外 tmux 不回落到 in-process（F2.5 不静默降级）
"""

from __future__ import annotations

import asyncio
import sys

import mewcode.team.backend.detect as detect_mod
import mewcode.team.backend.tmux as tmux_mod
from mewcode.team.backend import SpawnRequest
from mewcode.team.backend.detect import detect
from mewcode.team.backend.inprocess import InProcessBackend
from mewcode.team.backend.tmux import TmuxBackend, build_member_cmd
from mewcode.team.types import BackendType


def _req(**kw):
    base = {
        "team_name": "demo",
        "member_name": "alice",
        "agent_id": "agent-abc123",
        "worktree_path": "/wt/team-demo+alice",
        "session_dir": "/sess/s1",
        "agent_type": "general-purpose",
        "model": "",
        "initial_prompt": "do work",
        "plan_mode_required": True,
    }
    base.update(kw)
    return SpawnRequest(**base)


class TestDetect:
    def test_tmux_env_wins(self, monkeypatch):
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        assert detect() == BackendType.TMUX

    def test_iterm2(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
        monkeypatch.setattr(
            detect_mod.shutil, "which", lambda name: "/it2" if name == "it2" else None
        )
        assert detect() == BackendType.ITERM2

    def test_tmux_on_path(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.setattr(
            detect_mod.shutil, "which", lambda name: "/tmux" if name == "tmux" else None
        )
        assert detect() == BackendType.TMUX

    def test_inprocess_fallback(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.setattr(detect_mod.shutil, "which", lambda name: None)
        assert detect() == BackendType.IN_PROCESS


def test_build_member_cmd_has_agent_id_and_no_prompt():
    # 防的 bug：--agent-id 缺失 / initial_prompt 进命令行（F3.2/F2.6）
    cmd = build_member_cmd(_req())
    assert cmd[0] == sys.executable
    assert "--team-member" in cmd
    i = cmd.index("--agent-id")
    assert cmd[i + 1] == "agent-abc123"
    assert "--plan-mode" in cmd
    assert "do work" not in " ".join(cmd)


def test_tmux_backend_type():
    assert TmuxBackend().type() == BackendType.TMUX


def test_tmux_spawn_split_and_detached(monkeypatch):
    # 防的 bug：tmux 命令构造错误（split-window 参数 / new-session detached，F15/F16）
    captured = {}

    async def fake_exec(*args, **kw):
        captured["args"] = list(args)

        class P:
            returncode = 0

            async def communicate(self):
                return (b"%5\n", b"")

        return P()

    async def main():
        monkeypatch.setattr(tmux_mod.asyncio, "create_subprocess_exec", fake_exec)
        b = TmuxBackend()
        monkeypatch.setenv("TMUX", "/tmp/x,1,0")
        pane, aid = await b.spawn(_req())
        assert pane == "%5" and aid == "agent-abc123"
        assert captured["args"][:2] == ["tmux", "split-window"]
        assert "--agent-id" in captured["args"]
        monkeypatch.delenv("TMUX")
        captured.clear()
        await b.spawn(_req())
        assert captured["args"][1] == "new-session"  # 会话外 detached（F16）

    asyncio.run(main())


def test_tmux_spawn_failure_raises(monkeypatch):
    # 防的 bug：tmux 不可用静默降级 in-process（F2.5 不静默回退）
    async def fake_exec(*args, **kw):
        class P:
            returncode = 1

            async def communicate(self):
                return (b"", b"no server")

        return P()

    async def main():
        monkeypatch.setattr(tmux_mod.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.delenv("TMUX", raising=False)
        try:
            await TmuxBackend().spawn(_req())
            assert False, "应抛 BackendUnavailableError"
        except Exception as exc:  # noqa: BLE001 —— 断言抛的是 BackendUnavailableError
            assert "BackendUnavailable" in type(exc).__name__

    asyncio.run(main())


def test_inprocess_spawn_and_kill():
    class FakeTaskMgr:
        def __init__(self):
            self.stopped = []

        def launch(self, agent, task_text, *, name=None, **kw):
            assert task_text == "do work"
            return "agent-task1"

        def stop(self, aid):
            self.stopped.append(aid)

    async def main():
        tm = FakeTaskMgr()
        b = InProcessBackend(task_mgr=tm)
        assert b.type() == BackendType.IN_PROCESS
        pane, aid = await b.spawn(_req())
        assert pane == "" and aid == "agent-task1"  # F5.1
        await b.wake("", aid)  # no-op 不炸
        await b.kill("", aid)
        assert tm.stopped == ["agent-task1"]

    asyncio.run(main())
