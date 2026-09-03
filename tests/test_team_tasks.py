"""共享任务列表测试（ch15 F26-F30）。

防的 bug：
- add_blocked_by 不双向维护（F7.6）
- 重复 add 产生重复依赖（幂等）
- is_ready 不看 blocker 完成态（F7.5）
"""

from __future__ import annotations

import asyncio
import re

from mewcode.team.tasks import Filter, Patch, Status, Store, Task


def _store(tmp_path) -> Store:
    return Store(str(tmp_path / "tasks.json"))


def test_create_id_format_and_bidirectional(tmp_path):
    async def main():
        s = _store(tmp_path)
        t1 = Task(title="调查", assignee="alice")
        id1 = await s.create(t1)
        assert re.match(r"^task_[0-9a-f]{6}$", id1)
        id2 = await s.create(Task(title="修复", blocked_by=[id1]))
        assert (await s.get(id2)).blocked_by == [id1]
        assert (await s.get(id1)).blocks == [id2]  # 双向（F7.6）

    asyncio.run(main())


def test_is_ready_reflects_blockers(tmp_path):
    async def main():
        s = _store(tmp_path)
        id1 = await s.create(Task(title="a"))
        id2 = await s.create(Task(title="b", blocked_by=[id1]))
        by_id = {t.id: t for t in await s.list_(Filter())}
        assert by_id[id2].__dict__["is_ready"] is False  # 被未完成阻塞
        await s.update(id1, Patch(status=Status.COMPLETED))
        by_id2 = {t.id: t for t in await s.list_(Filter())}
        assert by_id2[id2].__dict__["is_ready"] is True

    asyncio.run(main())


def test_add_blocked_by_idempotent_bidirectional(tmp_path):
    # 防的 bug：重复 add_blocked_by 产生重复依赖项
    async def main():
        s = _store(tmp_path)
        id1 = await s.create(Task(title="a"))
        id2 = await s.create(Task(title="b"))
        await s.update(id2, Patch(add_blocked_by=[id1]))
        await s.update(id2, Patch(add_blocked_by=[id1]))  # 幂等
        assert (await s.get(id1)).blocks == [id2]
        assert (await s.get(id2)).blocked_by == [id1]
        await s.update(id2, Patch(remove_blocked_by=[id1]))
        assert (await s.get(id1)).blocks == []
        assert (await s.get(id2)).blocked_by == []

    asyncio.run(main())


def test_update_missing_returns_false(tmp_path):
    async def main():
        s = _store(tmp_path)
        assert await s.update("task_zzzzzz", Patch(status=Status.COMPLETED)) is False

    asyncio.run(main())


def test_status_filter(tmp_path):
    async def main():
        s = _store(tmp_path)
        await s.create(Task(title="p"))
        await s.update(await s.create(Task(title="c")), Patch(status=Status.COMPLETED))
        pending = await s.list_(Filter(status=Status.PENDING))
        assert len(pending) == 1 and pending[0].title == "p"

    asyncio.run(main())
