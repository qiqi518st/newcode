"""write_memory 工具测试（spec F13 记忆写入闭环的显式侧）

背景：Agent 收到「记住 X」时曾退化用 Bash 手动写记忆文件——用户级记忆在工作区外
被 L2 沙箱拦截（write_file DENY），Bash 又每次弹权限确认且中文乱码。write_memory
内部走 MemoryManager.store.apply：UTF-8 写入、自动索引、只写记忆命名空间、
MEMORY 权限类别四档免确认。
防 bug：写入走错 scope / 同标题重复创建 / 非法 type 误写 / 权限仍弹确认。
"""

import asyncio

from mewcode.memory.manager import MemoryManager
from mewcode.permission.checker import PermissionChecker, categorize
from mewcode.permission.modes import PermissionMode, ToolCategory
from mewcode.tools.memory_write import WriteMemoryTool


def _tool(tmp_path):
    manager = MemoryManager(tmp_path / "proj", tmp_path / "user")
    return WriteMemoryTool(manager), manager


def test_write_user_preference(tmp_path):
    """防 bug：user_preference 写入用户级目录且 UTF-8 内容正确。"""
    tool, manager = _tool(tmp_path)
    result = asyncio.run(
        tool.execute(
            {
                "type": "user_preference",
                "title": "偏好 any",
                "content": "用户要求用 any 替代 interface{}。",
            }
        )
    )
    assert result.status == "ok"
    assert "创建" in result.output
    note = manager.user_store.list_notes()[0]
    assert note.title == "偏好 any"
    assert "any" in (note.content or "")
    # 索引同步出现该行
    assert "偏好 any" in manager.user_store.load_index()


def test_write_project_knowledge_to_project_scope(tmp_path):
    """防 bug：project_knowledge 写入项目级目录而非用户级。"""
    tool, manager = _tool(tmp_path)
    result = asyncio.run(
        tool.execute(
            {
                "type": "project_knowledge",
                "title": "部署脚本",
                "content": "部署脚本在 scripts/ 目录。",
            }
        )
    )
    assert result.status == "ok"
    assert manager.project_store.list_notes(), "应写入项目级"
    assert not manager.user_store.list_notes()


def test_write_same_title_updates_not_duplicates(tmp_path):
    """防 bug：同 scope 相同标题应更新原文件，而非重复创建两条。"""
    tool, manager = _tool(tmp_path)
    asyncio.run(
        tool.execute(
            {
                "type": "user_preference",
                "title": "Python 初学者",
                "content": "用户是 Python 新手。",
            }
        )
    )
    first = manager.user_store.list_notes()[0]
    result = asyncio.run(
        tool.execute(
            {
                "type": "user_preference",
                "title": "Python 初学者",
                "content": "用户是 Python 新手，正在学习 Python。",
            }
        )
    )
    assert "更新" in result.output
    notes = manager.user_store.list_notes()
    assert len(notes) == 1, "应更新而非新建"
    assert notes[0].filename == first.filename
    assert "正在学习" in (notes[0].content or "")


def test_write_rejects_invalid_type(tmp_path):
    """防 bug：非法 type 应报错且不写任何文件。"""
    tool, manager = _tool(tmp_path)
    result = asyncio.run(
        tool.execute(
            {
                "type": "whatever",
                "title": "x",
                "content": "y",
            }
        )
    )
    assert result.status == "error"
    assert not manager.project_store.list_notes()
    assert not manager.user_store.list_notes()


def test_write_rejects_missing_fields(tmp_path):
    """防 bug：缺 title/content 应报错。"""
    tool, _ = _tool(tmp_path)
    assert (
        asyncio.run(tool.execute({"type": "user_preference", "title": "t"})).status
        == "error"
    )
    assert (
        asyncio.run(tool.execute({"type": "user_preference", "content": "c"})).status
        == "error"
    )
    assert asyncio.run(tool.execute({})).status == "error"


def test_write_memory_category_allow_all_modes(tmp_path):
    """防 bug：write_memory 归 MEMORY 类，四档权限模式全 ALLOW（不弹确认）。"""
    tool, _ = _tool(tmp_path)
    assert categorize("write_memory", tool.read_only) == ToolCategory.MEMORY
    assert categorize("read_memory", True) == ToolCategory.READONLY
    from mewcode.permission.modes import resolve_mode

    for mode in PermissionMode:
        decision = resolve_mode(mode, ToolCategory.MEMORY)
        assert decision.value == "allow", f"{mode} 下 write_memory 应 ALLOW"


def test_write_memory_not_readonly_and_executes_through_checker(tmp_path):
    """防 bug：write_memory 必须非只读，且经 PermissionChecker 流水线应 ALLOW。"""
    tool, _manager = _tool(tmp_path)
    assert tool.read_only is False
    from mewcode.provider.base import ToolCall

    checker = PermissionChecker.create(str(tmp_path))
    call = ToolCall(tool_name="write_memory", arguments={"type": "user_preference"})
    res = checker.check(call, is_interactive=True, read_only=False)
    assert res.decision.value == "allow"
