"""跨进程文件锁（ch15 F8.4）：mailbox 与 tasks 共用。

- `os.open(O_CREAT|O_EXCL|O_WRONLY)` 抢占式锁（EEXIST 表示被占）——Python 无跨平台
  flock 语义统一，走文件存在性抢占（TD-6）
- 拿不到锁 5-100ms 随机抖动重试 ≤10 次（防雪崩）
- 持锁超过 10s（`st_mtime`）视为 stale 直接删锁重试（崩溃残留兜底）
- `async with acquire(lock_path):` 用法；退出自动释放
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

LOCK_MAX_RETRIES: int = 10
LOCK_STALE_AFTER: float = 10.0
LOCK_BACKOFF_MIN: float = 0.005
LOCK_BACKOFF_MAX: float = 0.1


def _try_acquire(lock_path: str) -> bool:
    """尝试抢占锁文件；成功返回 True（持有 fd 直到 release）。"""
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def _try_break_stale(lock_path: str) -> bool:
    """锁文件持锁超 stale 阈值 → 删除重试；否则 False。"""
    try:
        st = Path(lock_path).stat()
    except OSError:
        return False
    if time.time() - st.st_mtime > LOCK_STALE_AFTER:
        try:
            os.unlink(lock_path)
        except OSError:
            return False
        return True
    return False


@asynccontextmanager
async def acquire(lock_path: str) -> AsyncIterator[None]:
    """抢占锁文件（F8.4）：失败抖动重试 ≤10 次；stale 删锁重试；退出释放。"""
    for _ in range(LOCK_MAX_RETRIES):
        if _try_acquire(lock_path):
            break
        if _try_break_stale(lock_path) and _try_acquire(lock_path):
            break
        await asyncio.sleep(random.uniform(LOCK_BACKOFF_MIN, LOCK_BACKOFF_MAX))
    else:
        raise TimeoutError(f"文件锁抢占失败（{LOCK_MAX_RETRIES} 次）: {lock_path}")
    try:
        yield
    finally:
        try:
            os.unlink(lock_path)
        except OSError:
            pass
