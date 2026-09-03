"""ch15 收尾测试：清理引导（守卫 + 纪律）+ 孤儿 worktree 清扫。

防的 bug：
- agent 手动 `git branch -D worktree-team-*` 对检出分支失败 + 弹窗吃 turn 到迭代上限
  → 守卫把「团队仍存在」的清理命令拦下来引导用 /team delete（F2）
- 守卫误伤 F15 收敛（git merge）/ 非团队 worktree（F2.5/F2.6）
- 团队配置已删的孤儿 team-* worktree 无人收（ch14 sweep 只匹配 agent-a[0-9a-f]{7}/wf-）
  → sweep_orphan_worktrees 自动清（F3），且 fail-closed 不清活跃团队/有变更的
- TeamDelete 后残留孤儿（删队时 best-effort 失败）→ 补扫（F3.4）
"""

from __future__ import annotations

import asyncio

import mewcode.team.manager as manager_mod
from mewcode.team.cleanup import TEAM_CLEANUP_DISCIPLINE, guard_team_git_cleanup
from mewcode.team.manager import Manager
from mewcode.team.tools import new_team_create_tool, new_team_delete_tool
from mewcode.team.types import BackendType
from mewcode.tools.shell import ExecuteCommandTool


class FakeWT:
    def __init__(self, name):
        self.name = name
        self.path = "/wt/" + name.replace("/", "+")


class FakeWTMgr:
    def __init__(self, wts):
        self._wts = list(wts)
        self.removed: list[tuple[str, bool]] = []

    def list(self):
        return list(self._wts)

    async def remove(self, name, opts):
        self.removed.append((name, opts.discard_changes))


def _mgr(tmp_path, monkeypatch, wtmgr=None):
    monkeypatch.setattr(manager_mod, "_detect_backend", lambda: BackendType.IN_PROCESS)
    return Manager(
        home_dir=str(tmp_path / "home"),
        project_root=str(tmp_path),
        wt_mgr=wtmgr,
        task_mgr=None,
    )


def test_discipline_constants():
    # 防的 bug：纪律段缺失（层1 落空）
    for kw in ("TeamDelete", "/team delete", "禁止"):
        assert kw in TEAM_CLEANUP_DISCIPLINE


def test_guard_blocks_existing_team(tmp_path, monkeypatch):
    async def main():
        mgr = _mgr(tmp_path, monkeypatch)
        await mgr.create("demo")
        # 团队仍存在 → 拦截（F2.2/F2.3）
        for cmd in (
            "git branch -D worktree-team-demo+alice",
            "git branch -d worktree-team-demo+alice worktree-team-demo+bob",
            "git worktree remove --force .mewcode/worktrees/team-demo+alice",
        ):
            hint = guard_team_git_cleanup(mgr, cmd)
            assert hint and "/team delete demo" in hint, cmd
            assert "禁止" in hint

    asyncio.run(main())


def test_guard_allows_normal_git(tmp_path, monkeypatch):
    async def main():
        mgr = _mgr(tmp_path, monkeypatch)
        await mgr.create("demo")
        # F15 收敛 + 日常命令 → 放行（F2.5）
        for cmd in (
            "git merge worktree-team-demo+alice --no-ff -m merge:alice",
            "git status",
            "git diff",
            "git log --oneline",
            "git add scratch/demo.md",
        ):
            assert guard_team_git_cleanup(mgr, cmd) is None, cmd

    asyncio.run(main())


def test_guard_allows_non_team_and_orphan(tmp_path, monkeypatch):
    async def main():
        mgr = _mgr(tmp_path, monkeypatch)
        await mgr.create("demo")
        # 非团队 worktree（F2.6）
        assert (
            guard_team_git_cleanup(mgr, "git branch -D worktree-agent-a1b2c3d") is None
        )
        # 团队已删（孤儿）→ 放行（F2.4）
        await mgr.delete("demo", force=True)
        assert (
            guard_team_git_cleanup(mgr, "git branch -D worktree-team-demo+alice")
            is None
        )

    asyncio.run(main())


def test_execute_command_guard_injection():
    # 防的 bug：守卫未注入时行为改变（N6）
    async def main():
        guard = lambda cmd: "BLOCKED" if "worktree-team-" in cmd else None
        tool = ExecuteCommandTool(guard=guard)
        r = await tool.execute({"command": "git branch -D worktree-team-demo+alice"})
        assert r.status == "error" and r.error == "BLOCKED"  # 不执行（F2.3）
        plain = ExecuteCommandTool()
        assert plain._guard is None  # 默认无守卫

    asyncio.run(main())


def test_sweep_orphan_worktrees(tmp_path, monkeypatch):
    # 防的 bug：孤儿 team-* worktree 无人收（F3.1）；fail-closed 不清活跃团队（F3.2）
    async def main():
        wts = [
            FakeWT("team-demo/alice"),  # demo 配置存在 → 保留
            FakeWT("team-orphan/bob"),  # 孤儿 → 删
            FakeWT("agent-a7235ad1"),  # 非团队 → 不动
            FakeWT("team-orphan2/carol"),  # 孤儿 → 删
        ]
        wtmgr = FakeWTMgr(wts)
        mgr = _mgr(tmp_path, monkeypatch, wtmgr=wtmgr)
        await mgr.create("demo")
        await mgr.sweep_orphan_worktrees()
        names = {n for n, _ in wtmgr.removed}
        assert names == {"team-orphan/bob", "team-orphan2/carol"}
        assert "team-demo/alice" not in names and "agent-a7235ad1" not in names
        assert all(dc is True for _, dc in wtmgr.removed)  # discard_changes=True

    asyncio.run(main())


def test_sweep_preserves_on_changes(tmp_path, monkeypatch):
    # 防的 bug：孤儿有未提交变更被强删（ch14 fail-closed 保护，F3.3）
    async def main():
        wtmgr = FakeWTMgr([FakeWT("team-orphan/bob")])

        async def remove_raises(name, opts):
            raise RuntimeError("worktree has changes")

        wtmgr.remove = remove_raises
        mgr = _mgr(tmp_path, monkeypatch, wtmgr=wtmgr)
        removed = await mgr.sweep_orphan_worktrees()
        assert removed == []  # 失败 → 保留

    asyncio.run(main())


def test_team_delete_triggers_sweep(tmp_path, monkeypatch):
    # 防的 bug：删队后残留孤儿不补扫（F3.4）
    async def main():
        mgr = _mgr(tmp_path, monkeypatch)
        swept = []
        mgr.sweep_orphan_worktrees = lambda: swept.append(1) or []
        await new_team_create_tool(mgr).execute({"team_name": "demo"})
        r = await new_team_delete_tool(mgr).execute(
            {"team_name": "demo", "force": True}
        )
        assert r.status == "ok"
        assert swept == [1]

    asyncio.run(main())
