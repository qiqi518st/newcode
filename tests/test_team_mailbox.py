"""Mailbox 测试（ch15 F8/F33）。

防的 bug：
- 并发写同一邮箱丢消息/截断（F8.4 锁文件串行）
- 崩溃残留锁文件永久阻塞（stale 10s 判定）
- 广播把发件人自己也算进去（F8.5）
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from newcode.team.filelock import LOCK_STALE_AFTER, acquire
from newcode.team.mailbox import Box, Message, MessageType


def _box(tmp_path) -> Box:
    return Box(str(tmp_path / "mailbox"))


def test_lock_serial(tmp_path):
    async def main():
        lock = str(tmp_path / "a.lock")
        async with acquire(lock):
            assert os.path.exists(lock)
        assert not os.path.exists(lock)

    asyncio.run(main())


def test_lock_stale(tmp_path):
    # 防的 bug：持锁进程崩溃残留 lock 永久阻塞
    async def main():
        lock = str(tmp_path / "stale.lock")
        Path(lock).write_text("x")
        old = time.time() - LOCK_STALE_AFTER - 1
        os.utime(lock, (old, old))
        async with acquire(lock):
            assert os.path.exists(lock)
        assert not os.path.exists(lock)

    asyncio.run(main())


def test_write_read_roundtrip(tmp_path):
    async def main():
        box = _box(tmp_path)
        await box.write(
            "alice",
            Message(from_="lead", to="alice", summary="hi there", content="hello"),
        )
        msgs = await box.read("alice")
        assert len(msgs) == 1
        m = msgs[0]
        assert m.from_ == "lead" and m.summary == "hi there" and m.content == "hello"
        assert m.read is False and m.timestamp > 0  # 落盘自动补时间戳、默认未读（F4.3）

    asyncio.run(main())


def test_read_unread_mark_read(tmp_path):
    async def main():
        box = _box(tmp_path)
        await box.write("a", Message(from_="lead", to="a", summary="m1"))
        await box.write("a", Message(from_="lead", to="a", summary="m2"))
        idx, unread = await box.read_unread("a")
        assert len(unread) == 2
        await box.mark_read("a", idx)
        _, unread2 = await box.read_unread("a")
        assert unread2 == []

    asyncio.run(main())


def test_concurrent_writes_no_loss(tmp_path):
    # 防的 bug：多协程同写一个邮箱互相覆盖（F8.4 文件锁串行）
    async def main():
        box = _box(tmp_path)

        async def w(i):
            await box.write(
                "x", Message(from_="m", to="x", summary=f"s{i}", content=str(i))
            )

        await asyncio.gather(*[w(i) for i in range(10)])
        assert len(await box.read("x")) == 10

    asyncio.run(main())


def test_broadcast_excludes_sender(tmp_path):
    # 防的 bug：广播把发件人自己也投一遍（F8.5）
    async def main():
        box = _box(tmp_path)
        delivered = await box.write_broadcast(
            "lead",
            Message(from_="lead", to="*", summary="bc"),
            ["alice", "bob", "lead"],
        )
        assert delivered == ["alice", "bob"]
        assert await box.read("lead") == []

    asyncio.run(main())


def test_message_type_roundtrip(tmp_path):
    m = Message(
        from_="lead",
        to="p",
        type=MessageType.PLAN_APPROVAL_RESPONSE,
        summary="ok",
        payload={"approve": True, "feedback": "go"},
    )
    d = m.to_dict()
    assert d["type"] == "plan_approval_response"
    m2 = Message.from_dict(d)
    assert m2.type == MessageType.PLAN_APPROVAL_RESPONSE
    assert m2.payload == {"approve": True, "feedback": "go"}
