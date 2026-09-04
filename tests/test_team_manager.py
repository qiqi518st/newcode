"""Team Manager 测试（ch15 F1-F10/F17）。

防的 bug：
- sanitize 保留 `.` 导致 `..` 作目录名路径遍历（N6）
- 跨进程 reload-before-modify：子进程持旧快照调 set_member_active 静默 no-op（F19c/F1.7）
- delete 非 force 放行活跃成员（F7）
- 坏 config.json 阻断启动（F1.4）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import newcode.team.manager as manager_mod
from newcode.team.mailbox import Box, Message
from newcode.team.manager import Manager
from newcode.team.persistence import sanitize
from newcode.team.types import (
    BackendType,
    MemberNotFoundError,
    TeamHasActiveMembersError,
    TeammateInfo,
    TeamNotFoundError,
)


def _make_mgr(tmp_path, monkeypatch):
    monkeypatch.setattr(manager_mod, "_detect_backend", lambda: BackendType.IN_PROCESS)
    return Manager(
        home_dir=str(tmp_path / "home"),
        project_root=str(tmp_path),
        wt_mgr=None,
        task_mgr=None,
    )


def test_sanitize_guards_traversal():
    # 防的 bug：`..` 原样通过 → ~/.newcode/teams/../ 逃逸（N6）
    assert sanitize("foo bar/baz") == "foo-bar-baz"
    for bad in ("", "   ", ".", ".."):
        with pytest.raises(ValueError):
            sanitize(bad)


def test_create_sanitize_suffix_lead(tmp_path, monkeypatch):
    async def main():
        mgr = _make_mgr(tmp_path, monkeypatch)
        t = await mgr.create("refactor auth", "desc")
        assert t.sanitized_name == "refactor-auth"
        assert t.backend == BackendType.IN_PROCESS
        data = json.loads(Path(t.config_path).read_text(encoding="utf-8"))
        assert data["members"][0]["name"] == "lead"
        assert data["members"][0]["is_active"] is None  # None 语义保留
        t2 = await mgr.create("refactor auth")  # 同名后缀
        assert t2.sanitized_name == "refactor-auth-2"

    asyncio.run(main())


def test_scan_restore_skips_corrupt(tmp_path, monkeypatch, capsys):
    async def main():
        m1 = _make_mgr(tmp_path, monkeypatch)
        await m1.create("demo")
        bad = Path(tmp_path) / "home" / ".newcode" / "teams" / "bad"
        bad.mkdir(parents=True)
        (bad / "config.json").write_text("{ not json", encoding="utf-8")
        m2 = _make_mgr(tmp_path, monkeypatch)
        assert set(m2.teams) == {"demo"}  # bad 被跳过
        assert "config.json 解析失败" in capsys.readouterr().err

    asyncio.run(main())


def test_member_ops_and_cross_process_reload(tmp_path, monkeypatch):
    async def main():
        # 防的 bug：子进程持旧快照（内存无 alice）调 set_member_active 静默 no-op（F1.7）
        mgr = _make_mgr(tmp_path, monkeypatch)
        t = await mgr.create("demo")
        alice = TeammateInfo(
            name="alice", agent_id="agent-a1", backend_type=BackendType.IN_PROCESS
        )
        await mgr.add_member(t, alice)
        t.members = [t.members[0]]  # 模拟子进程内存回到「只有 lead」的旧状态
        assert t.member_by_name("alice") is None
        await mgr.set_member_active(t, "alice", False)  # 必须经 reload 成功
        data = json.loads(Path(t.config_path).read_text(encoding="utf-8"))
        alice_disk = next(m for m in data["members"] if m["name"] == "alice")
        assert alice_disk["is_active"] is False
        assert mgr.is_teammate("agent-a1") is True
        with pytest.raises(MemberNotFoundError):
            await mgr.set_member_active(t, "nobody", False)

    asyncio.run(main())


def test_delete_active_member_rejected(tmp_path, monkeypatch):
    async def main():
        mgr = _make_mgr(tmp_path, monkeypatch)
        t = await mgr.create("demo")
        await mgr.add_member(
            t,
            TeammateInfo(
                name="alice", agent_id="agent-a1", backend_type=BackendType.IN_PROCESS
            ),
        )
        with pytest.raises(TeamHasActiveMembersError):
            await mgr.delete("demo", force=False)
        assert "demo" in mgr.teams  # 目录仍在
        with pytest.raises(TeamNotFoundError):
            await mgr.delete("nope", force=True)

    asyncio.run(main())


def test_delete_force_cleans(tmp_path, monkeypatch):
    async def main():
        mgr = _make_mgr(tmp_path, monkeypatch)
        t = await mgr.create("demo")
        assert Path(t.config_dir).is_dir()
        await mgr.delete("demo", force=True)
        assert "demo" not in mgr.teams
        assert not Path(t.config_dir).exists()

    asyncio.run(main())


def test_handle_task_done_idle_notification(tmp_path, monkeypatch):
    async def main():
        # 防的 bug：队员完成后 Lead 收不到 idle 通知、is_active 不回落（F12.1）
        mgr = _make_mgr(tmp_path, monkeypatch)
        t = await mgr.create("demo3")
        await mgr.add_member(
            t,
            TeammateInfo(
                name="bob", agent_id="agent-b1", backend_type=BackendType.IN_PROCESS
            ),
        )
        mgr.registry.register("bob", "agent-b1")
        await mgr.handle_task_done("agent-b1")
        box = Box(t.mailbox_dir)
        msgs = await box.read("lead")
        assert any("idle" in m.summary for m in msgs)
        data = json.loads(Path(t.config_path).read_text(encoding="utf-8"))
        assert data["members"][1]["is_active"] is False

    asyncio.run(main())


def test_poll_lead_mailboxes_marks_read(tmp_path, monkeypatch):
    async def main():
        mgr = _make_mgr(tmp_path, monkeypatch)
        t = await mgr.create("demo4")
        box = Box(t.mailbox_dir)
        await box.write(
            "lead", Message(from_="bob", to="lead", summary="more", content="x")
        )
        msgs = await mgr.poll_lead_mailboxes()
        assert any(m.summary == "more" for m in msgs)
        after = await box.read("lead")
        assert all(m.read for m in after)  # 消费后标 read（F11.3）

    asyncio.run(main())


def test_active_team_tracking(tmp_path, monkeypatch):
    async def main():
        mgr = _make_mgr(tmp_path, monkeypatch)
        await mgr.create("first")
        assert mgr.active_team().sanitized_name == "first"
        await mgr.create("second")
        assert mgr.active_team().sanitized_name == "second"
        await mgr.delete("second", force=True)
        assert mgr.active_team().sanitized_name == "first"
        await mgr.delete("first", force=True)
        assert mgr.active_team() is None

    asyncio.run(main())
