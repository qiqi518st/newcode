"""ch14 slash/commands/worktree.py /worktree 命令族测试（F9/AC17/AC18）。

防的 bug：
- accessor 未启用（None）时命令崩（应显示「Worktree 功能未启用」）
- create/exit/remove 异常泄漏到主流程（错误隔离 N6）
- exit/remove 的 --discard / --remove 旗标未传给 accessor（变更保护失效）
- 未知子命令静默吞掉
"""

from __future__ import annotations

import pytest

from mewcode.slash.commands.worktree import build
from mewcode.slash.ui import WorktreeSummary

pytestmark = pytest.mark.anyio


class _StubUI:
    def __init__(self, accessor):
        self._accessor = accessor
        self.messages: list[tuple[str, str]] = []

    def worktree_accessor(self):
        return self._accessor

    def show_message(self, text: str, style: str = "") -> None:
        self.messages.append((text, style))


class _Accessor:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.entered: list[str] = []
        self.exited: list[tuple] = []
        self.removed: list[tuple] = []
        self.rows: list[WorktreeSummary] = []

    async def create(self, name: str):
        self.created.append(name)
        return (f"/x/worktrees/{name}", f"worktree-{name}")

    def list(self):
        return self.rows

    async def enter(self, name: str) -> None:
        self.entered.append(name)

    async def exit(self, action: str, discard: bool) -> bool:
        self.exited.append((action, discard))
        return action == "remove"

    async def remove(self, name: str, discard: bool) -> None:
        self.removed.append((name, discard))


def _ctx(ui):
    class Ctx:
        pass

    c = Ctx()
    c.ui = ui
    return c


async def test_unregistered_shows_disabled():
    ui = _StubUI(None)
    await build()[0].handler(_ctx(ui), "list")
    assert "未启用" in ui.messages[0][0]


async def test_list_shows_rows():
    acc = _Accessor()
    acc.rows = [
        WorktreeSummary("alice", "/x/worktrees/alice", "worktree-alice", True, False),
        WorktreeSummary("bob", "/x/worktrees/bob", "worktree-bob", False, True),
    ]
    ui = _StubUI(acc)
    await build()[0].handler(_ctx(ui), "list")
    text = ui.messages[-1][0]
    assert "alice" in text and "worktree-alice" in text
    assert "[active]" in text
    assert "[手动]" in text


async def test_list_empty():
    ui = _StubUI(_Accessor())
    await build()[0].handler(_ctx(ui), "list")
    assert "No worktrees." in ui.messages[-1][0]


async def test_create():
    acc = _Accessor()
    ui = _StubUI(acc)
    await build()[0].handler(_ctx(ui), "create alice")
    assert acc.created == ["alice"]
    assert "已创建" in ui.messages[-1][0]
    assert "worktree-alice" in ui.messages[-1][0]


async def test_create_error_isolated():
    acc = _Accessor()

    async def bad_create(name):
        raise ValueError("slug 非法")

    acc.create = bad_create
    ui = _StubUI(acc)
    await build()[0].handler(_ctx(ui), "create '..'")
    assert "创建失败" in ui.messages[-1][0]


async def test_create_missing_slug():
    ui = _StubUI(_Accessor())
    await build()[0].handler(_ctx(ui), "create")
    assert "用法" in ui.messages[-1][0]


async def test_enter():
    acc = _Accessor()
    ui = _StubUI(acc)
    await build()[0].handler(_ctx(ui), "enter alice")
    assert acc.entered == ["alice"]
    assert "已进入 alice" in ui.messages[-1][0]


async def test_exit_keep_default():
    acc = _Accessor()
    ui = _StubUI(acc)
    await build()[0].handler(_ctx(ui), "exit")
    assert acc.exited == [("keep", False)]
    assert "已退出" in ui.messages[-1][0]


async def test_exit_remove_discard_flags():
    acc = _Accessor()
    ui = _StubUI(acc)
    await build()[0].handler(_ctx(ui), "exit --remove --discard")
    assert acc.exited == [("remove", True)]
    assert "已删除" in ui.messages[-1][0]


async def test_remove_discard_flag():
    acc = _Accessor()
    ui = _StubUI(acc)
    await build()[0].handler(_ctx(ui), "remove alice --discard")
    assert acc.removed == [("alice", True)]


async def test_remove_has_changes_error_isolated():
    acc = _Accessor()

    async def bad_remove(name, discard):
        raise RuntimeError("有未提交修改，拒绝删除")

    acc.remove = bad_remove
    ui = _StubUI(acc)
    await build()[0].handler(_ctx(ui), "remove alice")
    assert "删除失败" in ui.messages[-1][0]


async def test_unknown_subcommand():
    ui = _StubUI(_Accessor())
    await build()[0].handler(_ctx(ui), "frobnicate")
    assert "未知子命令" in ui.messages[-1][0]
