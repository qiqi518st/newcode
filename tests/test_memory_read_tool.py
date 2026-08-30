"""read_memory 工具测试（spec F13 加载闭环的读取侧）

背景：read_file 被路径沙箱锁在项目工作区内，用户级记忆在 ~/.mewcode/memory/
工作区外读不到；read_memory 按文件名跨项目/用户两级读全文，只读无副作用。
防 bug：忘记注册工具 / 读不到用户级记忆 / 越权读任意文件。
"""

import asyncio
from pathlib import Path

from mewcode.memory.manager import MemoryManager
from mewcode.memory.models import MemoryOperation
from mewcode.tools.memory_read import ReadMemoryTool


def _write_note(store, *, level, type_, title, slug, content):
    store.apply(
        MemoryOperation(
            action="create",
            level=level,
            type=type_,
            title=title,
            slug=slug,
            content=content,
        )
    )


def _tool(tmp_path, user_dir):
    manager = MemoryManager(tmp_path / "proj", user_dir)
    return ReadMemoryTool(manager), manager


def test_read_project_note(tmp_path):
    """防 bug：项目级记忆可按文件名读到全文。"""
    tool, _ = _tool(tmp_path, tmp_path / "user")
    _write_note(
        _manager_store(tmp_path / "proj"),
        level="project",
        type_="project_knowledge",
        title="python 水平",
        slug="python-level",
        content="用户 python 水平：中级，熟悉 pandas/numpy，不熟悉 asyncio。",
    )
    result = asyncio.run(
        tool.execute({"filename": "project_knowledge_python-level.md"})
    )
    assert result.status == "ok"
    assert "python 水平" in result.output


def test_read_user_note_across_scope(tmp_path):
    """防 bug：用户级记忆在 ~/.mewcode/memory（工作区外），read_file 沙箱读不到，
    read_memory 必须能读。"""
    user_dir = tmp_path / "user"
    tool, _ = _tool(tmp_path, user_dir)
    _write_note(
        _manager_store(user_dir),
        level="user",
        type_="user_preference",
        title="python 水平",
        slug="python-level",
        content="用户 python 水平：初级，会用基础语法，不熟装饰器。",
    )
    result = asyncio.run(tool.execute({"filename": "user_preference_python-level.md"}))
    assert result.status == "ok"
    assert "初级" in result.output


def test_read_missing_note(tmp_path):
    """防 bug：不存在的文件名应报错而非崩溃或返回空。"""
    tool, _ = _tool(tmp_path, tmp_path / "user")
    result = asyncio.run(tool.execute({"filename": "no-such-note.md"}))
    assert result.status == "error"
    assert "记忆不存在" in result.error


def test_read_rejects_bad_args(tmp_path):
    """防 bug：空/非字符串 filename 应报错。"""
    tool, _ = _tool(tmp_path, tmp_path / "user")
    assert asyncio.run(tool.execute({})).status == "error"
    assert asyncio.run(tool.execute({"filename": 123})).status == "error"


def test_read_only_and_registered(tmp_path):
    """防 bug：read_memory 必须只读（Plan Mode 下可用），且注册进注册表后能被导出。"""
    from mewcode.tools.registry import Registry

    tool, _ = _tool(tmp_path, tmp_path / "user")
    assert tool.read_only is True
    registry = Registry.default()
    registry.register(tool)  # 模拟 main.py 的注册方式
    assert registry.get("read_memory") is tool
    names = [t.name for t in registry.to_definitions()]
    assert "read_memory" in names
    assert "read_memory" in [t.name for t in registry.read_only_definitions()]


def _manager_store(directory: Path):
    """直接构造 MemoryStore 以绕过 MemoryManager 内部，保持与 manager 同目录"""
    from mewcode.memory.store import MemoryStore

    return MemoryStore(directory)
