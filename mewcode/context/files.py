"""最近访问文件追踪（spec F19/F20：纯净字节记录，锁保护，压缩后恢复数据源）。"""

import asyncio
import time
from dataclasses import dataclass

from ..context.constants import MAX_RECENT_FILES


@dataclass
class TrackedFile:
    """单条文件快照。"""

    path: str  # 绝对路径（避免相对路径在不同 cwd 下错乱）
    content: str  # 纯净字节（不带行号、不带截断提示）
    timestamp_ns: int  # 最后一次成功读取的单调时间戳


class FileTracker:
    """最近访问文件追踪（会话级，锁保护，按时间倒序保留）。"""

    def __init__(self) -> None:
        self._files: dict[str, TrackedFile] = {}
        self._lock = asyncio.Lock()

    async def record(self, path: str, content: str) -> None:
        """记录/覆盖一条文件快照，时间戳取当前单调时钟。

        若新时间戳与已有最大时间戳相同（时钟分辨率不足时），自增 1 保证
        严格单调递增——recent 按时间戳倒序时最近记录的稳定排最前（spec F16）。
        """
        async with self._lock:
            ts = time.monotonic_ns()
            existing = self._files.get(path)
            if existing is not None and ts <= existing.timestamp_ns:
                ts = existing.timestamp_ns + 1
            # 即便本 path 是覆盖，也要确保 ts 大于当前所有记录的最大值
            max_ts = max((f.timestamp_ns for f in self._files.values()), default=0)
            if ts <= max_ts:
                ts = max_ts + 1
            self._files[path] = TrackedFile(
                path=path,
                content=content,
                timestamp_ns=ts,
            )

    async def recent(self, limit: int = MAX_RECENT_FILES) -> list[TrackedFile]:
        """按时间戳倒序取前 limit 个（返回拷贝，不暴露内部，spec F20）。

        返回 dataclass 的浅拷贝（content/path/timestamp 均为不可变快照），
        调用方改动不影响 tracker 内部状态。
        """
        async with self._lock:
            items = sorted(self._files.values(), key=lambda f: f.timestamp_ns, reverse=True)
            return [
                TrackedFile(path=f.path, content=f.content, timestamp_ns=f.timestamp_ns)
                for f in items[:limit]
            ]
