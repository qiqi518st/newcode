"""队员 spawn 测试（ch15 F10/F25）。

防的 bug：
- in-process 队员再 spawn 未被拦截（TD-14）
- dont_ask 未覆盖角色（F6.3 队员无 TUI 接 ApprovalRequest 会卡死）
- plan_mode_required 无硬门控（F7.4 写工具提前可用）
- extra_tools 未注入 → 队员看不到协作工具（N2）
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import mewcode.team.manager as manager_mod
from mewcode.agent.team_hook import (
    TeammateContext,
    TeamSpawnRequest,
    set_current_teammate,
)
from mewcode.subagent.types import AgentDefinition
from mewcode.team.manager import Manager
from mewcode.team.spawn import TeamHookImpl
from mewcode.team.types import BackendType


class FakeWT:
    def __init__(self, path):
        self.path = path
        self.branch = "worktree-" + path.replace("/", "+")


class FakeWTMgr:
    def __init__(self):
        self.created = []

    async def create(self, name, base, manual):
        self.created.append(name)
        return FakeWT("/wt/" + name.replace("/", "+"))


class FakeTaskMgr:
    def __init__(self):
        self.launched = []

    def launch(self, agent, task_text, *, name=None, **kw):
        self.launched.append((name, task_text))
        return "agent-task9"

    def stop(self, aid):
        pass


ROLE = AgentDefinition(name="general-purpose", description="d", body="you are gp")


class FakeCatalog:
    def resolve(self, name):
        return ROLE if name == "general-purpose" else None

    def fork_definition(self):
        return None

    def list(self):
        return [ROLE]


class FakeLauncher:
    def __init__(self):
        self.calls = []
        self.last_sub = None

    def make_sub_agent(self, role, **kw):
        self.calls.append(kw)
        sub = SimpleNamespace(permission=None, _dont_ask=kw.get("dont_ask"))
        sub.set_allowed_tools = lambda allowed: setattr(sub, "_allowed", allowed)
        sub.registry = SimpleNamespace(names=list)
        self.last_sub = sub
        return sub, SimpleNamespace()


class FakeFeatures:
    fork_teammate = False


def _hook(tmp_path, monkeypatch):
    monkeypatch.setattr(manager_mod, "_detect_backend", lambda: BackendType.IN_PROCESS)
    tm = FakeTaskMgr()
    mgr = Manager(
        home_dir=str(tmp_path / "home"),
        project_root=str(tmp_path),
        wt_mgr=FakeWTMgr(),
        task_mgr=tm,
    )
    return TeamHookImpl(mgr, FakeCatalog(), FakeLauncher(), FakeFeatures()), mgr


def test_spawn_inprocess_full_flow(tmp_path, monkeypatch):
    async def main():
        impl, mgr = _hook(tmp_path, monkeypatch)
        await mgr.create("demo")
        out = await impl.spawn_teammate(
            TeamSpawnRequest(
                team_name="demo",
                prompt="调研",
                subagent_type="general-purpose",
                name="alice",
            )
        )
        data = json.loads(out)
        assert data["member_name"] == "alice"
        assert data["agent_id"] == "agent-task9"
        assert data["backend"] == "in-process"
        assert mgr.member_of("agent-task9") is not None
        assert mgr.registry.resolve("alice") == "agent-task9"
        # launcher 参数（F6.3 dont_ask / N2 extra_tools / F10.5 runtime+teammate）
        lk = impl._launcher
        assert lk.calls[0]["dont_ask"] is True
        assert "send_message" in lk.calls[0]["extra_tools"]
        assert lk.calls[0]["runtime"] is not None
        assert lk.calls[0]["teammate"] is not None
        # worktree 命名
        assert "team-demo/alice" in impl._mgr.wt_mgr.created

    asyncio.run(main())


def test_plan_gated_initial_allowed_tools(tmp_path, monkeypatch):
    async def main():
        # 防的 bug：plan_mode_required 成员初始即可写文件（F7.4 硬门控）
        impl, mgr = _hook(tmp_path, monkeypatch)
        await mgr.create("demo")
        await impl.spawn_teammate(
            TeamSpawnRequest(
                team_name="demo", prompt="p", name="planner", plan_mode_required=True
            )
        )
        assert impl._launcher.calls[0]["permission_mode"] is not None  # PLAN 模式起步
        allowed = impl._launcher.last_sub._allowed
        assert allowed is not None
        assert "write_file" not in allowed and "edit_file" not in allowed  # 硬门控
        assert "read_file" in allowed and "send_message" in allowed

    asyncio.run(main())


def test_inprocess_member_cannot_spawn(tmp_path, monkeypatch):
    async def main():
        # 防的 bug：in-process 队员再 spawn 未被拦截（TD-14/F10.1）
        impl, mgr = _hook(tmp_path, monkeypatch)
        await mgr.create("demo")
        set_current_teammate(
            TeammateContext(
                team_name="demo",
                member_name="alice",
                agent_id="agent-x",
                backend_type="in-process",
            )
        )
        try:
            with pytest.raises(Exception) as ei:
                await impl.spawn_teammate(
                    TeamSpawnRequest(team_name="demo", prompt="x", name="bob")
                )
            assert "InProcessTeammateNoSpawn" in type(ei.value).__name__
        finally:
            set_current_teammate(None)

    asyncio.run(main())


def test_spawn_unknown_team(tmp_path, monkeypatch):
    async def main():
        impl, _ = _hook(tmp_path, monkeypatch)
        out = await impl.spawn_teammate(TeamSpawnRequest(team_name="nope", prompt="x"))
        assert json.loads(out)["status"] == "error"

    asyncio.run(main())


def test_spawn_unknown_role(tmp_path, monkeypatch):
    async def main():
        impl, mgr = _hook(tmp_path, monkeypatch)
        await mgr.create("demo")
        out = await impl.spawn_teammate(
            TeamSpawnRequest(team_name="demo", prompt="x", subagent_type="nonexistent")
        )
        assert json.loads(out)["status"] == "error"

    asyncio.run(main())
