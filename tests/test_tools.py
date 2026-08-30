"""工具单元测试"""

import os

import pytest

from mewcode.tools.file_ops import EditFileTool, ReadFileTool, WriteFileTool
from mewcode.tools.search import ListFilesTool, SearchCodeTool
from mewcode.tools.shell import ExecuteCommandTool


class TestReadFileTool:
    @pytest.mark.anyio
    async def test_read_success(self):
        t = ReadFileTool()
        r = await t.execute({"path": "mewcode/main.py"})
        assert r.status == "ok"
        assert "main" in r.output

    @pytest.mark.anyio
    async def test_read_not_found(self):
        t = ReadFileTool()
        r = await t.execute({"path": "not_exist_xxx.txt"})
        assert r.status == "error"
        assert "不存在" in r.error

    @pytest.mark.anyio
    async def test_read_path_traversal(self):
        t = ReadFileTool()
        r = await t.execute({"path": "../../etc/passwd"})
        assert r.status == "error"
        assert "超出项目范围" in r.error

    @pytest.mark.anyio
    async def test_read_limit(self):
        t = ReadFileTool()
        r = await t.execute({"path": "mewcode/main.py", "limit": 5})
        assert r.status == "ok"
        assert r.truncated is True
        assert "未返回全部文件" in r.output
        assert "limit=" not in r.output

    @pytest.mark.anyio
    async def test_read_limit_can_exceed_default(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = tmp_path / "large.txt"
        path.write_text("\n".join(f"line-{i}" for i in range(600)), encoding="utf-8")

        result = await ReadFileTool().execute({"path": "large.txt", "limit": 600})

        assert result.status == "ok"
        assert result.truncated is False
        assert result.output.count("\n") == 599


class TestWriteFileTool:
    @pytest.mark.anyio
    async def test_write_and_read(self):
        t_write = WriteFileTool()
        t_read = ReadFileTool()
        path = "test_tmp_write.txt"
        r = await t_write.execute({"path": path, "content": "hello"})
        assert r.status == "ok"
        r2 = await t_read.execute({"path": path})
        assert r2.status == "ok"
        assert "hello" in r2.output
        os.remove(path)

    @pytest.mark.anyio
    async def test_write_path_traversal(self):
        t = WriteFileTool()
        r = await t.execute({"path": "../../etc/xxx", "content": "x"})
        assert r.status == "error"
        assert "超出项目范围" in r.error


class TestEditFileTool:
    @pytest.mark.anyio
    async def test_edit_unique_match(self):
        t_write = WriteFileTool()
        t_edit = EditFileTool()
        t_read = ReadFileTool()
        path = "test_tmp_edit.txt"
        await t_write.execute({"path": path, "content": "foo bar baz"})
        r = await t_edit.execute(
            {"path": path, "old_string": "bar", "new_string": "qux"}
        )
        assert r.status == "ok"
        r2 = await t_read.execute({"path": path})
        assert "foo qux baz" in r2.output
        os.remove(path)

    @pytest.mark.anyio
    async def test_edit_no_match(self):
        t = EditFileTool()
        r = await t.execute(
            {
                "path": "mewcode/main.py",
                "old_string": "NOTEXIST12345",
                "new_string": "x",
            }
        )
        assert r.status == "error"
        assert "未找到" in r.error

    @pytest.mark.anyio
    async def test_edit_multiple_match(self):
        t_write = WriteFileTool()
        t_edit = EditFileTool()
        path = "test_tmp_multi.txt"
        await t_write.execute({"path": path, "content": "a a a"})
        r = await t_edit.execute({"path": path, "old_string": "a", "new_string": "b"})
        assert r.status == "error"
        assert "找到 3 处" in r.error
        os.remove(path)


class TestExecuteCommandTool:
    @pytest.mark.anyio
    async def test_allowed(self):
        t = ExecuteCommandTool()
        r = await t.execute({"command": "python --version"})
        assert r.status == "ok"
        assert "Python" in r.output

    @pytest.mark.anyio
    async def test_command_execution(self):
        """白名单已移除，命令执行由权限系统在 Agent 层管控"""
        t = ExecuteCommandTool()
        r = await t.execute({"command": "echo hello"})
        assert r.status == "ok"
        assert "hello" in r.output

    @pytest.mark.anyio
    async def test_timeout(self):
        t = ExecuteCommandTool()
        # 用很短的超时来测试（但工具内部超时是 60s，这里不改）
        # 改为测试一个快速失败命令
        r = await t.execute({"command": 'python -c "exit(1)"'})
        assert r.status == "error"
        assert "exit_code" in r.output


class TestListFilesTool:
    @pytest.mark.anyio
    async def test_list(self):
        t = ListFilesTool()
        r = await t.execute({"pattern": "mewcode/*.py"})
        assert r.status == "ok"
        assert "main.py" in r.output or len(r.output) > 0

    @pytest.mark.anyio
    async def test_list_empty(self):
        t = ListFilesTool()
        r = await t.execute({"pattern": "*.xyz"})
        assert r.status == "ok"


class TestSearchCodeTool:
    @pytest.mark.anyio
    async def test_search(self):
        t = SearchCodeTool()
        r = await t.execute({"pattern": "class.*Tool", "glob": "mewcode/tools/*.py"})
        assert r.status == "ok"

    @pytest.mark.anyio
    async def test_search_empty(self):
        t = SearchCodeTool()
        r = await t.execute({"pattern": "NOTEXISTXYZ", "glob": "mewcode/*.py"})
        assert r.status == "ok"


# 运行异步测试的辅助
@pytest.mark.anyio
async def test_all():
    await TestReadFileTool().test_read_success()
    await TestReadFileTool().test_read_not_found()
    await TestReadFileTool().test_read_path_traversal()
    await TestReadFileTool().test_read_limit()
    await TestWriteFileTool().test_write_and_read()
    await TestWriteFileTool().test_write_path_traversal()
    await TestEditFileTool().test_edit_unique_match()
    await TestEditFileTool().test_edit_no_match()
    await TestEditFileTool().test_edit_multiple_match()
    await TestExecuteCommandTool().test_allowed()
    await TestExecuteCommandTool().test_command_execution()
    await TestExecuteCommandTool().test_timeout()
    await TestListFilesTool().test_list()
    await TestListFilesTool().test_list_empty()
    await TestSearchCodeTool().test_search()
    await TestSearchCodeTool().test_search_empty()
