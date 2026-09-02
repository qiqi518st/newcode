"""ch14 tools/agent_worktree.py _execute_with_worktree 测试（F8.2/F8.3/F8.4 + 隔离沙箱）。

防的 bug：
- 子 Agent 工具未注入 worktree cwd（相对路径写到主目录，隔离失效）
- 隔离子 Agent 权限沙箱根未指向 worktree（绝对路径可写出 worktree，F4.4 失效）
- 隔离子 Agent 写权限未自动放行（acceptEdits 缺失 → worktree 内 write_file 被拒）
- 完成后未调 auto_cleanup（无价值 worktree 残留）
- 有变更时未保留并追加保留通知（主 Agent 拿不到 worktree 位置去 review）
- notice 未注入 task 文本（子 Agent 不知道自己换了工作目录）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mewcode.permission.modes import PermissionMode
from mewcode.subagent.types import AgentDefinition, Source
from mewcode.tools.agent_worktree import _execute_with_worktree, build_worktree_notice
from mewcode.tools.cwd import cwd_from_ctx, resolve_path
from mewcode.worktree.config import WorktreesConfig
from mewcode.worktree.manager import Manager

pytestmark = pytest.mark.anyio


def _manager(repo: Path) -> Manager:
    return Manager(str(repo), WorktreesConfig())


def _role() -> AgentDefinition:
    return AgentDefinition(name="wt", description="d", source=Source.BUILTIN)


class _FakeLauncher:
    """记录 make_sub_agent 的隔离参数；返回注入的 stub 子 Agent。"""

    def __init__(self, sub) -> None:
        self._sub = sub
        self.calls: list[tuple] = []

    def make_sub_agent(
        self,
        role,
        *,
        is_background=False,
        model_override="",
        permission_mode=None,
        sandbox_root=None,
    ):
        self.calls.append(
            (role.name, permission_mode, sandbox_root, is_background, model_override)
        )
        return (self._sub, object())


class _ReadOnlyStub:
    """只读子 Agent：断言 ctx cwd 已注入 worktree，不写文件。"""

    def __init__(self) -> None:
        self.seen_cwd: str | None = None
        self.task_text = ""

    async def run_to_completion(self, task: str) -> str:
        self.task_text = task
        self.seen_cwd = cwd_from_ctx()
        return "完成分析，无改动。"


class _WriterStub:
    """写文件子 Agent：相对路径写入 worktree（验证 cwd 生效 + 变更保留）。"""

    def __init__(self) -> None:
        self.wrote_to: str | None = None

    async def run_to_completion(self, task: str) -> str:
        p = Path(resolve_path("new.txt"))
        p.write_text("x\n", encoding="utf-8")
        self.wrote_to = str(p)
        return "我新建了一个文件。"


async def test_execute_readonly_creates_then_cleanup(git_repo):
    m = _manager(git_repo)
    stub = _ReadOnlyStub()
    launcher = _FakeLauncher(stub)
    result = await _execute_with_worktree(m, launcher, _role(), "请分析代码")
    assert result.status == "completed"
    # 子 Agent 在 worktree 内执行
    assert stub.seen_cwd is not None
    assert "/.mewcode/worktrees/agent-a" in stub.seen_cwd
    # 隔离构造参数：acceptEdits + 沙箱根=worktree
    _name, pm, root, bg, _model = launcher.calls[0]
    assert pm == PermissionMode.ACCEPT_EDITS
    assert "/.mewcode/worktrees/agent-a" in root
    assert bg is False  # 强制前台
    # notice 注入 task 文本
    assert "<worktree-context>" in stub.task_text
    # 无改动 → auto_cleanup 已删除 worktree（不残留）
    assert m.list() == []
    assert "Worktree 保留在" not in result.text


async def test_execute_writer_keeps_with_notice(git_repo):
    m = _manager(git_repo)
    stub = _WriterStub()
    launcher = _FakeLauncher(stub)
    result = await _execute_with_worktree(m, launcher, _role(), "新建 new.txt")
    assert result.status == "completed"
    # 子 Agent 的相对路径落到 worktree 而非主目录
    assert "/.mewcode/worktrees/agent-a" in stub.wrote_to
    assert not (git_repo / "new.txt").exists()  # 主目录未污染
    # 有变更 → 保留 + 追加保留通知给主 Agent review
    assert "[Worktree 保留在" in result.text
    assert "分支 worktree-agent-a" in result.text
    assert len(m.list()) == 1


def test_build_worktree_notice_contains_paths():
    n = build_worktree_notice("/parent", "/parent/.mewcode/worktrees/agent-a1b2c3de")
    assert "<worktree-context>" in n
    assert "/parent" in n
    assert "agent-a1b2c3de" in n
