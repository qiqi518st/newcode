"""MemoryStore / MemoryManager 单测（ch09 T10/T11，spec F11-F14 / AC18-AC23）。

防 bug：记忆写到错误作用域、非法文件名路径注入、索引超过 200 行/25KB、
LLM 返回非法 JSON/provider 错误导致主会话崩溃、并发更新互相踩踏。
"""

import json

import pytest

from newcode.memory.manager import MemoryManager
from newcode.memory.models import TYPE_SCOPE, MemoryOperation
from newcode.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory")


def _op(**kw):
    return MemoryOperation(**kw)


# ---------- MemoryStore ----------


def test_type_scope_whitelist():
    """防 bug：四类笔记必须映射到规定作用域，不允许跨级。"""
    assert TYPE_SCOPE == {
        "user_preference": "user",
        "correction_feedback": "user",
        "project_knowledge": "project",
        "reference_material": "project",
    }


def test_create_writes_file_with_frontmatter(store):
    """防 bug：create 生成带完整 frontmatter 的 Markdown（AC18）。"""
    note = store.apply(
        _op(
            action="create",
            level="user",
            type="user_preference",
            title="简洁回复",
            slug="terse_replies",
            content="用户偏好简短回复。",
        ),
        source_session="20260820-000000-aaaa",
    )
    assert note.filename == "user_preference_terse_replies.md"
    text = (store.directory / note.filename).read_text(encoding="utf-8")
    assert "type: user_preference" in text
    assert "scope: user" in text
    assert "title: 简洁回复" in text
    assert "created:" in text and "updated:" in text
    assert "source_session: 20260820-000000-aaaa" in text
    assert "status: active" in text


def test_scope_mismatch_rejected(store):
    """防 bug：LLM 不能把用户偏好写到项目级（作用域白名单）。"""
    with pytest.raises(ValueError):
        store.apply(
            _op(
                action="create",
                level="project",
                type="user_preference",
                title="x",
                slug="x",
                content="y",
            )
        )


def test_project_knowledge_to_project_store(tmp_path):
    """防 bug：项目知识只能写入项目级 store，且 type/scope 匹配。"""
    s = MemoryStore(tmp_path / "project-memory")
    # level=project 时 apply 到项目 store 是合法的
    note = s.apply(
        _op(
            action="create",
            level="project",
            type="project_knowledge",
            title="API",
            slug="api",
            content="约定",
        )
    )
    assert note.scope == "project"


def test_filename_path_injection_rejected(store):
    """防 bug：文件名含目录分隔符或路径穿越必须被拒绝（AC23）。"""
    for bad in ["../../evil.md", "a/b.md", "..%2f.md", "a b.md"]:
        with pytest.raises(ValueError):
            store.apply(
                _op(
                    action="create",
                    level="project",
                    type="project_knowledge",
                    filename=bad,
                    content="x",
                )
            )


def test_update_rewrites_frontmatter(store):
    """防 bug：update 重写正文并更新 updated 时间，但保留 created。"""
    first = store.apply(
        _op(
            action="create",
            level="user",
            type="user_preference",
            title="T",
            slug="t",
            content="v1",
        ),
        source_session="s1",
    )
    second = store.apply(
        _op(
            action="update",
            level="user",
            filename=first.filename,
            title="T2",
            content="v2",
        ),
        source_session="s2",
    )
    assert second.created == first.created
    assert second.content == "v2"
    assert second.updated >= first.updated


def test_delete_removes_file_and_index(store):
    """防 bug：delete 删除笔记文件并从索引移除。"""
    note = store.apply(
        _op(
            action="create",
            level="user",
            type="user_preference",
            title="D",
            slug="d",
            content="x",
        )
    )
    store.apply(_op(action="delete", level="user", filename=note.filename))
    assert not (store.directory / note.filename).exists()
    assert "user_preference_d.md" not in store.load_index()


def test_index_sync_create(store):
    """防 bug：create 后 MEMORY.md 出现对应行（AC19）。"""
    store.apply(
        _op(
            action="create",
            level="project",
            type="project_knowledge",
            title="测试命令",
            slug="test_cmd",
            content="运行 pytest",
        )
    )
    idx = store.load_index()
    assert "- [project_knowledge] 测试命令" in idx
    assert "运行 pytest" in idx


def test_index_200_line_limit(store):
    """防 bug：索引超过 200 行时拒绝本次变更并保留旧状态。"""
    for i in range(200):
        store.apply(
            _op(
                action="create",
                level="user",
                type="user_preference",
                title=f"note{i}",
                slug=f"n{i}",
                content="x",
            )
        )
    with pytest.raises(ValueError):
        store.apply(
            _op(
                action="create",
                level="user",
                type="user_preference",
                title="overflow",
                slug="overflow",
                content="x",
            )
        )
    idx = store.load_index()
    assert "overflow" not in idx
    # 原子性：被拒笔记的文件也不应残留（spec：失败保留旧文件和旧索引）
    assert not (store.directory / "user_preference_overflow.md").exists()
    assert len(store.list_notes()) == 200


def test_index_25kb_limit(tmp_path):
    """防 bug：索引超过 25KB 时拒绝写入而非写坏。"""
    d = tmp_path / "m"
    d.mkdir()
    # 直接构造 170 个长笔记文件（约 34KB 索引，行数 170 < 200，确保触发 25KB 而非行数）
    for i in range(170):
        (d / f"user_preference_big{i}.md").write_text(
            "---\ntype: user_preference\ntitle: "
            + "X" * 60
            + "\n---\n\n"
            + "Y" * 200
            + "\n",
            encoding="utf-8",
        )
    store = MemoryStore(d)
    with pytest.raises(ValueError):
        store.apply(
            _op(
                action="create",
                level="user",
                type="user_preference",
                title="overflow",
                slug="overflow",
                content="x",
            )
        )
    assert not (d / "user_preference_overflow.md").exists()


def test_index_truncation_defensive(tmp_path):
    """防 bug：读取端对超限索引防御性截断并追加 (index truncated)。"""
    d = tmp_path / "m"
    d.mkdir()
    idx = d / "MEMORY.md"
    # 手写一个超 25KB 的索引
    line = "- [user_preference] title - content\n"
    big = line * 4000  # ~120KB
    idx.write_text(big, encoding="utf-8")
    store = MemoryStore(d)
    out = store.load_index()
    assert "(index truncated)" in out
    assert len(out.encode("utf-8")) <= 26 * 1024


def test_clear_removes_notes(store):
    """防 bug：clear 删除该作用域全部笔记并重建空索引。"""
    for i in range(3):
        store.apply(
            _op(
                action="create",
                level="user",
                type="user_preference",
                title=f"c{i}",
                slug=f"c{i}",
                content="x",
            )
        )
    assert store.clear() == 3
    assert store.list_notes() == []
    assert store.load_index() == ""


def test_list_notes_sorted(store):
    """防 bug：list_notes 按 updated 倒序且跳过 MEMORY.md。"""
    a = store.apply(
        _op(
            action="create",
            level="user",
            type="user_preference",
            title="a",
            slug="a",
            content="x",
        )
    )
    store.apply(
        _op(
            action="create",
            level="user",
            type="user_preference",
            title="b",
            slug="b",
            content="x",
        )
    )
    # 更新 a 使其 updated 最新
    store.apply(_op(action="update", level="user", filename=a.filename, content="y"))
    names = [n.filename for n in store.list_notes()]
    assert names == [a.filename, "user_preference_b.md"]


# ---------- MemoryManager ----------


class MockProvider:
    """返回固定文本流的 provider（无工具）。"""

    def __init__(self, text: str):
        self._text = text
        self.model = "mock-model"
        self.last_prompt = None

    async def stream(self, payload):
        self.last_prompt = payload
        yield type("E", (), {"text": self._text})()


def _manager(tmp_path, provider_text="[]"):
    manager = MemoryManager(
        tmp_path / "proj-memory",
        tmp_path / "user-memory",
        provider=MockProvider(provider_text),
        model="mock-model",
    )
    return manager


def test_load_indexes_project_first(tmp_path):
    """防 bug：项目索引在前、用户索引在后，且空级不产生空段（AC20）。"""
    proj = tmp_path / "proj-memory"
    user = tmp_path / "user-memory"
    MemoryStore(proj).apply(
        _op(
            action="create",
            level="project",
            type="project_knowledge",
            title="P",
            slug="p",
            content="pc",
        )
    )
    MemoryStore(user).apply(
        _op(
            action="create",
            level="user",
            type="user_preference",
            title="U",
            slug="u",
            content="uc",
        )
    )
    manager = MemoryManager(proj, user)
    text = manager.load_indexes()
    assert text.index("- [project_knowledge] P") < text.index("- [user_preference] U")


@pytest.mark.anyio
async def test_update_async_create(tmp_path):
    """防 bug：mock LLM 返回 create 时按作用域写入笔记（AC18）。"""
    payload = json.dumps(
        [
            {
                "action": "create",
                "level": "user",
                "type": "user_preference",
                "title": "简洁",
                "slug": "terse",
                "content": "偏好简洁",
            }
        ]
    )
    manager = _manager(tmp_path, payload)
    out = await manager.update_async([], session_id="s1")
    assert len(out) == 1
    assert (tmp_path / "user-memory" / "user_preference_terse.md").exists()
    # 项目目录不能出现用户偏好
    assert not (tmp_path / "proj-memory" / "user_preference_terse.md").exists()


@pytest.mark.anyio
async def test_update_async_empty_noop(tmp_path):
    """防 bug：LLM 返回空数组时不产生任何文件。"""
    manager = _manager(tmp_path, "[]")
    out = await manager.update_async([], session_id="s1")
    assert out == []
    assert manager.load_indexes() == ""


@pytest.mark.anyio
async def test_update_async_invalid_json(tmp_path):
    """防 bug：非法 JSON 只记录日志返回空，不崩溃主会话（AC22）。"""
    manager = _manager(tmp_path, "not-json{")
    out = await manager.update_async([], session_id="s1")
    assert out == []
    assert manager.load_indexes() == ""


@pytest.mark.anyio
async def test_update_async_not_array(tmp_path):
    """防 bug：LLM 返回对象而非数组时安全拒绝。"""
    manager = _manager(tmp_path, '{"action":"create"}')
    out = await manager.update_async([], session_id="s1")
    assert out == []


@pytest.mark.anyio
async def test_update_async_provider_error(tmp_path):
    """防 bug：provider 抛异常时记忆更新失败但主流程不受影响。"""

    class Boom:
        model = "mock"

        async def stream(self, payload):
            raise RuntimeError("provider down")
            yield  # pragma: no cover

    manager = MemoryManager(
        tmp_path / "p", tmp_path / "u", provider=Boom(), model="mock"
    )
    out = await manager.update_async([], session_id="s1")
    assert out == []


@pytest.mark.anyio
async def test_update_async_scope_mismatch_rejected(tmp_path):
    """防 bug：LLM 越级写入被本地白名单拦截（AC23）。"""
    payload = json.dumps(
        [
            {
                "action": "create",
                "level": "project",
                "type": "user_preference",
                "title": "越级",
                "slug": "x",
                "content": "x",
            }
        ]
    )
    manager = _manager(tmp_path, payload)
    out = await manager.update_async([], session_id="s1")
    assert out == []
    assert manager.load_indexes() == ""


@pytest.mark.anyio
async def test_update_async_delete(tmp_path):
    """防 bug：delete 操作从对应作用域删除文件。"""
    store = MemoryStore(tmp_path / "user-memory")
    note = store.apply(
        _op(
            action="create",
            level="user",
            type="user_preference",
            title="D",
            slug="d",
            content="x",
        )
    )
    payload = json.dumps(
        [{"action": "delete", "level": "user", "filename": note.filename}]
    )
    manager = _manager(tmp_path, payload)
    out = await manager.update_async([], session_id="s1")
    assert out == [None]
    assert not (tmp_path / "user-memory" / note.filename).exists()


@pytest.mark.anyio
async def test_update_async_concurrent_dedup(tmp_path):
    """防 bug：同一作用域同时只允许一个更新任务，后到者返回 None 不并发写。"""
    import asyncio

    calls = 0

    class Slow:
        model = "mock"

        async def stream(self, payload):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            yield type("E", (), {"text": "[]"})()

    manager = MemoryManager(tmp_path / "p", tmp_path / "u", provider=Slow(), model="m")
    # 并发触发两个更新
    task_a = asyncio.create_task(manager.update_async([], session_id="s"))
    await asyncio.sleep(0.01)
    task_b = asyncio.create_task(manager.update_async([], session_id="s"))
    await asyncio.gather(task_a, task_b)
    # 只有一个实际执行了 provider 请求
    assert calls == 1


def test_manager_without_provider_loads_indexes(tmp_path):
    """防 bug：provider 未选定时仍可加载索引（启动阶段）。"""
    proj = tmp_path / "p"
    MemoryStore(proj).apply(
        _op(
            action="create",
            level="project",
            type="project_knowledge",
            title="P",
            slug="p",
            content="c",
        )
    )
    manager = MemoryManager(proj, tmp_path / "u")
    assert "P" in manager.load_indexes()
