"""ch14 tools/agent_tool.py AgentTool isolation 分支测试（F8.2/F8.4/F11.2）。

防的 bug：
- isolation:worktree 角色仍走普通 launch_defined（没创建 worktree，隔离失效）
- worktree_mgr=None 或 enable=false 时隔离不降级（F11.2 应回落普通路径）
- Fork 路径被 isolation 影响（Fork 应始终走原路径，F8.5）
"""

from __future__ import annotations

import pytest

from newcode.subagent.launcher import LaunchResult
from newcode.subagent.types import AgentDefinition, Source
from newcode.tools.agent_tool import AgentTool
from newcode.worktree.config import WorktreesConfig
from newcode.worktree.manager import Manager

pytestmark = pytest.mark.anyio


class _FakeLauncher:
    """记录调用；make_sub_agent 返回 stub 子 Agent。"""

    def __init__(self) -> None:
        self.defined_calls: list = []
        self.fork_calls: list = []
        self.made_calls: list = []
        self.sub_agent = None

    def make_sub_agent(
        self,
        role,
        *,
        is_background=False,
        model_override="",
        permission_mode=None,
        sandbox_root=None,
    ):
        self.made_calls.append((role.name, permission_mode, sandbox_root))
        return (self.sub_agent, object())

    async def launch_defined(
        self, role_name, prompt, *, name=None, background=False, model_override=""
    ):
        self.defined_calls.append((role_name, prompt, background, model_override))
        return LaunchResult(status="completed", text=f"defined:{role_name}")

    async def launch_fork(self, prompt, *, name=None):
        self.fork_calls.append(prompt)
        return LaunchResult(status="completed", text="forked")


class _FakeCatalog:
    def __init__(self, role):
        self._role = role

    def resolve(self, name):
        return self._role if self._role and name == self._role.name else None

    def list(self):
        return [self._role] if self._role else []


class _FakeParent:
    conv = type("C", (), {"get_context": lambda self: []})()


class _StubSub:
    """无副作用子 Agent：run_to_completion 直接返回文本。"""

    async def run_to_completion(self, task: str) -> str:
        return "done"


def _role(isolation: str = "") -> AgentDefinition:
    return AgentDefinition(
        name="wt", description="d", isolation=isolation, source=Source.BUILTIN
    )


def _tool(launcher, role, worktree_mgr, cfg):
    return AgentTool(
        _FakeCatalog(role),
        launcher,
        lambda: _FakeParent(),
        worktree_mgr=worktree_mgr,
        worktrees_cfg=cfg,
    )


async def test_isolation_worktree_routes_to_worktree(git_repo):
    """F8.2：isolation:worktree 角色 → worktree 分支（创建→执行→自动清理）。"""
    m = Manager(str(git_repo), WorktreesConfig())
    launcher = _FakeLauncher()
    launcher.sub_agent = _StubSub()
    tool = _tool(launcher, _role("worktree"), m, WorktreesConfig())
    result = await tool.execute({"prompt": "做点事", "subagent_type": "wt"})
    assert result.status == "ok"
    assert result.output == "done"
    assert launcher.defined_calls == []  # 未走 launch_defined
    assert m.list() == []  # 无改动 → 自动清理


async def test_no_isolation_routes_to_launch_defined(git_repo):
    """F8.5：isolation 为空 → 原 launch_defined 路径。"""
    m = Manager(str(git_repo), WorktreesConfig())
    launcher = _FakeLauncher()
    launcher.sub_agent = _StubSub()
    tool = _tool(launcher, _role(""), m, WorktreesConfig())
    result = await tool.execute({"prompt": "做点事", "subagent_type": "wt"})
    assert result.status == "ok"
    assert result.output == "defined:wt"
    assert launcher.defined_calls  # 走了普通路径


async def test_worktree_mgr_none_degrades(git_repo):
    """F11.2：worktree_mgr=None → 降级走 launch_defined。"""
    launcher = _FakeLauncher()
    launcher.sub_agent = _StubSub()
    tool = _tool(launcher, _role("worktree"), None, WorktreesConfig())
    result = await tool.execute({"prompt": "做点事", "subagent_type": "wt"})
    assert result.output == "defined:wt"
    assert launcher.defined_calls


async def test_enable_false_degrades(git_repo):
    """F11.2：worktrees.enable=false → 隔离角色降级，不建目录。"""
    m = Manager(str(git_repo), WorktreesConfig())
    m.cfg.enable = False
    launcher = _FakeLauncher()
    launcher.sub_agent = _StubSub()
    tool = _tool(launcher, _role("worktree"), m, WorktreesConfig())
    result = await tool.execute({"prompt": "做点事", "subagent_type": "wt"})
    assert result.output == "defined:wt"
    assert m.list() == []  # 未建 worktree


async def test_fork_path_unaffected(git_repo):
    """F8.5：Fork（无 subagent_type）始终走原 launch_fork。"""
    m = Manager(str(git_repo), WorktreesConfig())
    launcher = _FakeLauncher()
    launcher.sub_agent = _StubSub()
    tool = _tool(launcher, _role("worktree"), m, WorktreesConfig())
    result = await tool.execute({"prompt": "fork 任务"})
    assert result.output == "forked"
    assert launcher.fork_calls


async def test_dynamic_isolation_param_routes_to_worktree(git_repo):
    """F9：调用 isolation='worktree' 动态覆盖角色（角色未声明）→ worktree 分支。"""
    from newcode.permission.modes import PermissionMode

    m = Manager(str(git_repo), WorktreesConfig())
    launcher = _FakeLauncher()
    launcher.sub_agent = _StubSub()
    tool = _tool(launcher, _role(""), m, WorktreesConfig())  # 角色 isolation 为空
    result = await tool.execute(
        {"prompt": "做点事", "subagent_type": "wt", "isolation": "worktree"}
    )
    assert result.status == "ok"
    assert result.output == "done"
    assert launcher.defined_calls == []  # 未走普通路径
    assert m.list() == []  # 无改动 → 自动清理
    # 隔离构造参数：acceptEdits + 沙箱根=worktree（worktree 内写自动放行）
    _role_name, pm, root = launcher.made_calls[0]
    assert pm == PermissionMode.ACCEPT_EDITS
    assert "/.newcode/worktrees/agent-a" in root


async def test_dynamic_isolation_none_overrides_role(git_repo):
    """F9：调用 isolation='none' 覆盖角色声明（角色 isolation=worktree）→ 普通路径。"""
    m = Manager(str(git_repo), WorktreesConfig())
    launcher = _FakeLauncher()
    launcher.sub_agent = _StubSub()
    tool = _tool(launcher, _role("worktree"), m, WorktreesConfig())
    result = await tool.execute(
        {"prompt": "做点事", "subagent_type": "wt", "isolation": "none"}
    )
    assert result.output == "defined:wt"
    assert launcher.defined_calls


async def test_dynamic_isolation_unavailable_errors():
    """F9：动态请求 isolation='worktree' 但 worktree 不可用 → 结构化错误（不静默降级）。"""
    launcher = _FakeLauncher()
    launcher.sub_agent = _StubSub()
    tool = _tool(launcher, _role(""), None, WorktreesConfig())
    result = await tool.execute(
        {"prompt": "做点事", "subagent_type": "wt", "isolation": "worktree"}
    )
    assert result.status == "error"
    assert "不可用" in result.error


def test_agent_tool_schema_has_isolation():
    """F9：agent 工具 schema 含 isolation 参数（动态指定通道）。"""
    tool = AgentTool(_FakeCatalog(_role("")), _FakeLauncher(), lambda: _FakeParent())
    props = tool.parameters["properties"]
    assert "isolation" in props
    assert props["isolation"]["enum"] == ["worktree", "none"]
