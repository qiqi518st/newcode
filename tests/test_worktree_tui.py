"""ch14 tui 层 WorktreeAdapter + REPL active_cwd 注入测试（F9.3/F10.3）。

防的 bug：
- enter 后 REPL.active_cwd 未更新（主 Agent 后续工具仍指向主目录，隔离失效）
- exit/remove 后 active_cwd 未清空（残留指向已删 worktree）
- 无 worktree_mgr 时 worktree_accessor() 崩（应返回 None）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from newcode.tui.app import REPL
from newcode.tui.worktree_adapter import WorktreeAdapter
from newcode.worktree.config import WorktreesConfig
from newcode.worktree.manager import Manager

pytestmark = pytest.mark.anyio


class _CwdHolder:
    value: str = ""


def _manager(repo: Path) -> Manager:
    return Manager(str(repo), WorktreesConfig())


def _adapter(m: Manager) -> tuple[WorktreeAdapter, _CwdHolder]:
    holder = _CwdHolder()
    return WorktreeAdapter(m, lambda cwd: setattr(holder, "value", cwd)), holder


async def test_adapter_enter_sets_active_cwd(git_repo):
    m = _manager(git_repo)
    adapter, holder = _adapter(m)
    path, _ = await adapter.create("alice")
    await adapter.enter("alice")
    assert holder.value == path  # active_cwd 指向 worktree
    removed = await adapter.exit("keep", False)
    assert removed is False
    assert holder.value == ""  # exit 后清空


async def test_adapter_enter_exit_remove_flags(git_repo):
    m = _manager(git_repo)
    adapter, holder = _adapter(m)
    await adapter.create("alice")
    await adapter.enter("alice")
    removed = await adapter.exit("remove", True)  # --remove --discard
    assert removed is True
    assert holder.value == ""
    assert m.get("alice") is None


async def test_adapter_list_marks_active(git_repo):
    m = _manager(git_repo)
    adapter, _ = _adapter(m)
    await adapter.create("alice")
    await adapter.enter("alice")
    rows = adapter.list()
    assert len(rows) == 1
    assert rows[0].active is True
    assert rows[0].manual is True
    assert rows[0].branch == "worktree-alice"


async def test_adapter_remove_non_active_keeps_cwd(git_repo):
    m = _manager(git_repo)
    adapter, holder = _adapter(m)
    await adapter.create("alice")
    await adapter.create("bob")
    await adapter.enter("alice")
    await adapter.remove("bob", False)
    assert holder.value != ""  # alice 仍在，active_cwd 不受影响
    assert len(m.list()) == 1


async def test_adapter_remove_active_clears_cwd(git_repo):
    m = _manager(git_repo)
    adapter, holder = _adapter(m)
    await adapter.create("alice")
    await adapter.enter("alice")
    await adapter.remove("alice", True)
    assert holder.value == ""


def test_repl_effective_cwd_default(tmp_path, monkeypatch):
    repl = object.__new__(REPL)
    repl.active_cwd = ""
    monkeypatch.chdir(tmp_path)
    assert repl._effective_cwd() == str(tmp_path)


def test_repl_effective_cwd_active():
    repl = object.__new__(REPL)
    repl.active_cwd = "/wt/path"
    assert repl._effective_cwd() == "/wt/path"


def test_repl_worktree_accessor_none_when_no_mgr():
    repl = object.__new__(REPL)
    repl.worktree_mgr = None
    repl._worktree_accessor = None
    assert repl.worktree_accessor() is None


async def test_repl_worktree_accessor_wraps_manager(git_repo):
    m = _manager(git_repo)
    repl = object.__new__(REPL)
    repl.worktree_mgr = m
    repl._worktree_accessor = None
    repl.active_cwd = ""
    acc = repl.worktree_accessor()
    assert acc is not None
    assert repl.worktree_accessor() is acc  # 缓存复用
    path, _ = await acc.create("alice")
    await acc.enter("alice")
    assert repl.active_cwd == path  # 主 Agent 下次 Run 注入该 cwd
