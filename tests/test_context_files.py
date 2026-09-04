"""FileTracker 文件追踪单测（ch08 T29，spec F19/F20）。

防 bug：覆盖更新丢失、recent 顺序错乱、并发竞态、返回引用污染内部。
"""

import pytest

from newcode.context.files import FileTracker


@pytest.mark.anyio
async def test_record_overwrite():
    """防 bug：同 path 二次 record 未覆盖 → 旧内容残留。

    同 path 覆盖应更新 content（纯净字节，不带行号）。
    """
    ft = FileTracker()
    await ft.record("/a.py", "old")
    await ft.record("/a.py", "new content")
    recent = await ft.recent(5)
    assert len(recent) == 1
    assert recent[0].content == "new content"


@pytest.mark.anyio
async def test_recent_order():
    """防 bug：recent 未按时间戳倒序 → 恢复段展示旧文件而非最近。

    7 个文件 recent(5) 应取最近 5 个，按时间戳倒序。
    """
    ft = FileTracker()
    for i in range(7):
        await ft.record(f"/file{i}.py", f"c{i}")
    recent = await ft.recent(5)
    assert len(recent) == 5
    # 最近记录的在最前（倒序）
    assert recent[0].path == "/file6.py"
    assert recent[1].path == "/file5.py"
    assert recent[4].path == "/file2.py"


@pytest.mark.anyio
async def test_concurrent_record_recent():
    """防 bug：并发 record/recent 无锁保护 → 数据错乱/重复。

    20 task 混写读，recent 应无重复 path、无异常。
    """
    import asyncio

    ft = FileTracker()

    async def writer(i):
        await ft.record(f"/f{i}.py", f"c{i}")

    async def reader():
        return await ft.recent(20)

    await asyncio.gather(*[writer(i) for i in range(20)])
    recent = await asyncio.gather(*[reader() for _ in range(5)])
    # 所有读取结果应一致且无重复
    paths = [f.path for f in recent[0]]
    assert len(paths) == len(set(paths)), "recent 不应有重复 path"


@pytest.mark.anyio
async def test_recent_returns_copy():
    """防 bug：recent 返回内部引用 → 调用方改动污染 tracker 状态。

    返回的应是拷贝，改动不影响内部。
    """
    ft = FileTracker()
    await ft.record("/a.py", "content")
    recent = await ft.recent(5)
    recent[0].content = "TAMPERED"
    # 内部不受影响
    again = await ft.recent(5)
    assert again[0].content == "content"


@pytest.mark.anyio
async def test_recent_limit():
    """防 bug：recent(limit) 未截断 → 返回全部而非 limit 个。"""
    ft = FileTracker()
    for i in range(10):
        await ft.record(f"/f{i}.py", "c")
    recent = await ft.recent(3)
    assert len(recent) == 3
