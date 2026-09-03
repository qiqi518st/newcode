"""ch14 六个核心工具在 ctx cwd 下解析相对路径测试（AC13/AC14）。

防的 bug：
- read_file/write_file/edit_file 仍按进程 cwd 解析相对路径（worktree 隔离失效，写到主目录）
- execute_command 子进程未设 cwd（bash 在 worktree 里跑了主目录的命令）
- ctx 注入改工具 schema（F7.4：工具列表稳定、prompt cache 不抖）
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mewcode.tools.cwd import cwd_from_ctx, with_cwd
from mewcode.tools.file_ops import EditFileTool, ReadFileTool, WriteFileTool
from mewcode.tools.search import ListFilesTool, SearchCodeTool
from mewcode.tools.shell import ExecuteCommandTool

pytestmark = pytest.mark.anyio


async def test_write_read_in_ctx_cwd(tmp_path):
    """write_file + read_file 相对路径落在 ctx cwd（AC13）。"""
    wt = tmp_path / "wt"
    wt.mkdir()
    with with_cwd(str(wt)):
        r = await WriteFileTool().execute({"path": "a.txt", "content": "hi"})
        assert r.status == "ok"
        r = await ReadFileTool().execute({"path": "a.txt"})
        assert r.status == "ok" and "hi" in r.output
    assert (wt / "a.txt").read_text(encoding="utf-8") == "hi"
    assert not (Path.cwd() / "a.txt").exists()  # 主目录未被污染


async def test_edit_in_ctx_cwd(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with with_cwd(str(wt)):
        await WriteFileTool().execute({"path": "f.txt", "content": "hello world"})
        r = await EditFileTool().execute(
            {"path": "f.txt", "old_string": "hello", "new_string": "hi"}
        )
        assert r.status == "ok"
        r = await ReadFileTool().execute({"path": "f.txt"})
        assert "hi world" in r.output


async def test_list_files_in_ctx_cwd(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "x.py").write_text("", encoding="utf-8")
    (wt / "y.md").write_text("", encoding="utf-8")
    with with_cwd(str(wt)):
        r = await ListFilesTool().execute({"pattern": "*.py"})
        assert r.status == "ok"
        assert "x.py" in r.output
        assert "y.md" not in r.output


async def test_search_code_in_ctx_cwd(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
    with with_cwd(str(wt)):
        r = await SearchCodeTool().execute({"pattern": "foo"})
        assert r.status == "ok"
        assert "a.py" in r.output


async def test_execute_command_uses_ctx_cwd(tmp_path):
    """AC14：execute_command 子进程 cwd 跟随 ctx cwd。"""
    wt = tmp_path / "wt"
    wt.mkdir()
    with with_cwd(str(wt)):
        r = await ExecuteCommandTool().execute({"command": "pwd"})
        assert r.status == "ok"
        assert str(wt) in r.output


async def test_execute_command_explicit_cwd_wins(tmp_path):
    """显式 cwd 参数优先于 ctx cwd（既有行为保持）。"""
    wt1 = tmp_path / "wt1"
    wt2 = tmp_path / "wt2"
    wt1.mkdir()
    wt2.mkdir()
    with with_cwd(str(wt1)):
        r = await ExecuteCommandTool().execute({"command": "pwd", "cwd": str(wt2)})
        assert r.status == "ok"
        assert str(wt2) in r.output


async def test_cross_context_aclose_no_crash():
    """防的 bug：async 生成器在异 context 被 aclose（GC finalizer / 子 Agent 后台交互）
    → with_cwd 的 reset 抛「created in a different Context」→ Unhandled exception in event loop。

    修复：reset 容错（吞 RuntimeError/ValueError + 当前 context 清默认）；不抛即通过。
    """

    async def _gen():
        with with_cwd("/some/wt"):
            yield 1

    g = _gen()
    assert await g.__anext__() == 1

    async def _close():
        await g.aclose()

    await asyncio.create_task(
        _close()
    )  # 修复前在此抛「created in a different Context」


def test_with_cwd_sync_reset():
    """正常路径复位仍正确（未被容错逻辑破坏）。"""
    with with_cwd("/a"):
        assert cwd_from_ctx() == "/a"
    assert cwd_from_ctx() is None


def test_schema_unchanged():
    """F7.4：ctx 注入不改变工具 parameters（主 Agent 工具列表稳定）。"""
    for tool in (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ListFilesTool(),
        SearchCodeTool(),
        ExecuteCommandTool(),
    ):
        props = tool.parameters["properties"]
        assert "ctx_cwd" not in props
        assert "worktree" not in props
        assert "context" not in props
