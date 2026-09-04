"""团队工具测试（ch15 F7/F20-F34）：7 工具 + 可见性 + 广播 + 权限。

防的 bug：
- 协作工具对普通子 Agent 可见 / 主 Agent 非团队态可见（N2）
- plan_approval_response 队员可发（F8.6 仅 Lead）
- SendMessage 目标解析失败不报错（F34.2）
"""

from __future__ import annotations

import asyncio
import json

import newcode.team.manager as manager_mod
from newcode.agent.team_hook import (
    TeammateContext,
    current_teammate,
    set_current_teammate,
)
from newcode.team.manager import Manager
from newcode.team.tools import (
    new_send_message_tool,
    new_task_create_tool,
    new_task_get_tool,
    new_task_list_tool,
    new_task_update_tool,
    new_team_create_tool,
    new_team_delete_tool,
)
from newcode.team.types import BackendType, TeammateInfo
from newcode.tools.filter import GLOBAL_DENY, TEAMMATE_EXTRA_TOOLS


def _make_mgr(tmp_path, monkeypatch):
    monkeypatch.setattr(manager_mod, "_detect_backend", lambda: BackendType.IN_PROCESS)
    return Manager(
        home_dir=str(tmp_path / "home"),
        project_root=str(tmp_path),
        wt_mgr=None,
        task_mgr=None,
    )


def test_collab_tools_hidden_from_plain_subagents():
    # 防的 bug：普通子 Agent 工具池泄漏协作工具（N2）
    assert TEAMMATE_EXTRA_TOOLS <= GLOBAL_DENY


def test_team_create_delete(tmp_path, monkeypatch):
    async def main():
        mgr = _make_mgr(tmp_path, monkeypatch)
        r = await new_team_create_tool(mgr).execute({"team_name": "demo"})
        assert r.status == "ok"
        assert json.loads(r.output)["team_name"] == "demo"
        r = await new_team_delete_tool(mgr).execute(
            {"team_name": "demo", "force": True}
        )
        assert r.status == "ok"
        assert mgr.get("demo") is None

    asyncio.run(main())


def test_task_tools_crud(tmp_path, monkeypatch):
    async def main():
        mgr = _make_mgr(tmp_path, monkeypatch)
        await new_team_create_tool(mgr).execute({"team_name": "demo"})
        r = await new_task_create_tool(mgr).execute(
            {"title": "调研", "assignee": "alice"}
        )
        task_id = json.loads(r.output)["task_id"]
        r = await new_task_list_tool(mgr).execute({})
        tasks = json.loads(r.output)
        assert any(t["id"] == task_id and t["is_ready"] is True for t in tasks)
        r = await new_task_get_tool(mgr).execute({"task_id": task_id})
        assert json.loads(r.output)["title"] == "调研"
        r = await new_task_update_tool(mgr).execute(
            {"task_id": task_id, "status": "completed"}
        )
        assert r.status == "ok"

    asyncio.run(main())


def test_send_message_broadcast_and_permissions(tmp_path, monkeypatch):
    async def main():
        mgr = _make_mgr(tmp_path, monkeypatch)
        team = await mgr.create("demo")
        await mgr.add_member(
            team,
            TeammateInfo(
                name="bob", agent_id="agent-b1", backend_type=BackendType.IN_PROCESS
            ),
        )
        await mgr.add_member(
            team,
            TeammateInfo(
                name="carol", agent_id="agent-c1", backend_type=BackendType.IN_PROCESS
            ),
        )
        mgr.registry.register("bob", "agent-b1")
        mgr.registry.register("carol", "agent-c1")
        sm = new_send_message_tool(mgr)
        r = await sm.execute({"to": "bob", "summary": "ping", "message": "hello"})
        assert json.loads(r.output)["delivered_to"] == ["agent-b1"]
        # 广播（F8.5）
        r = await sm.execute({"to": "*", "summary": "bc", "message": "all"})
        d = json.loads(r.output)["delivered_to"]
        assert "agent-b1" in d and "agent-c1" in d
        # 无法解析目标（F34.2）
        r = await sm.execute({"to": "nobody", "summary": "x"})
        assert r.status == "error"
        # plan_approval_response 仅 Lead（F8.6）：队员上下文 → 拒绝
        set_current_teammate(
            TeammateContext(
                team_name="demo",
                member_name="bob",
                agent_id="agent-b1",
                backend_type="in-process",
            )
        )
        try:
            r = await sm.execute(
                {
                    "to": "lead",
                    "type": "plan_approval_response",
                    "payload": {"approve": True},
                }
            )
            assert r.status == "error"
        finally:
            set_current_teammate(None)
        assert current_teammate() is None

    asyncio.run(main())
