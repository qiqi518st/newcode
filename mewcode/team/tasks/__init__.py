"""共享任务列表（ch15 F26-F30）：Store——tasks.json 单文件 + 文件锁。

- 存储：`<team_config_dir>/tasks.json`（F30），read-modify-write + 原子写
- 锁：`tasks.lock`（复用 team/filelock，TD-6）
- update 双向维护 blocked_by / blocks（F7.6/F29）
- list_ 附加 is_ready（不存盘，F7.5）
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from ..filelock import acquire
from ..persistence import atomic_write_json
from .filter import Filter, Patch, Status, Task, is_ready

__all__ = ["Filter", "Patch", "Status", "Store", "Task", "is_ready"]


class Store:
    """Team 内共享任务清单（F26-F30）。"""

    def __init__(self, path: str) -> None:
        self._path = str(Path(path))
        self._lock_path = str(Path(path).with_suffix(path[path.rfind(".") :] + ".lock"))
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)

    def _read_all(self) -> dict[str, Task]:
        """读全部任务（无锁——调用方在锁内）。"""
        if not Path(self._path).exists():
            return {}
        import json

        try:
            data = json.loads(Path(self._path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        tasks_raw = data.get("tasks", []) if isinstance(data, dict) else []
        if not isinstance(tasks_raw, list):
            return {}
        return {
            t.id: t
            for t in (Task.from_dict(x) for x in tasks_raw if isinstance(x, dict))
        }

    def _write_all(self, tasks: dict[str, Task]) -> None:
        atomic_write_json(
            self._path,
            {"tasks": [t.to_dict() for t in tasks.values()]},
        )

    async def create(self, t: Task) -> str:
        """新建任务，返回 task_id（task_<6位hex>，F7.3）。"""
        t.id = f"task_{secrets.token_hex(3)}"
        now = int(time.time())
        t.created_at = now
        t.updated_at = now
        async with acquire(self._lock_path):
            tasks = self._read_all()
            # 双向依赖：把自己加进各 blocked_by 任务的 blocks（去重，幂等）
            for dep_id in t.blocked_by:
                if dep_id in tasks and t.id not in tasks[dep_id].blocks:
                    tasks[dep_id].blocks.append(t.id)
            tasks[t.id] = t
            self._write_all(tasks)
        return t.id

    async def get(self, id_: str) -> Task | None:
        async with acquire(self._lock_path):
            return self._read_all().get(id_)

    async def list_(self, f: Filter | None = None) -> list[Task]:
        f = f or Filter()
        async with acquire(self._lock_path):
            tasks = self._read_all()
        result = [t for t in tasks.values() if f.matches(t)]
        for t in result:
            t.__dict__["is_ready"] = is_ready(t, tasks)  # 附加字段不存盘
        return sorted(result, key=lambda t: t.created_at)

    async def update(self, id_: str, p: Patch) -> bool:
        """更新任务（F7.6）：字段 + 依赖双向维护；返回是否存在。"""
        async with acquire(self._lock_path):
            tasks = self._read_all()
            t = tasks.get(id_)
            if t is None:
                return False
            if p.title is not None:
                t.title = p.title
            if p.description is not None:
                t.description = p.description
            if p.status is not None:
                t.status = p.status
            if p.assignee is not None:
                t.assignee = p.assignee
            # 依赖双向维护：self 的 blocks ← 对方被本任务阻塞（去重，幂等）
            for dep_id in p.add_blocks:
                if dep_id in tasks and dep_id != id_ and dep_id not in t.blocks:
                    t.blocks.append(dep_id)
                    if id_ not in tasks[dep_id].blocked_by:
                        tasks[dep_id].blocked_by.append(id_)
            for dep_id in p.remove_blocks:
                if dep_id in t.blocks:
                    t.blocks.remove(dep_id)
                    if dep_id in tasks:
                        tasks[dep_id].blocked_by = [
                            x for x in tasks[dep_id].blocked_by if x != id_
                        ]
            # 依赖双向维护：self 的 blocked_by ← 对方阻塞本任务（去重，幂等）
            for dep_id in p.add_blocked_by:
                if dep_id in tasks and dep_id != id_ and dep_id not in t.blocked_by:
                    t.blocked_by.append(dep_id)
                    if id_ not in tasks[dep_id].blocks:
                        tasks[dep_id].blocks.append(id_)
            for dep_id in p.remove_blocked_by:
                if dep_id in t.blocked_by:
                    t.blocked_by.remove(dep_id)
                    if dep_id in tasks:
                        tasks[dep_id].blocks = [
                            x for x in tasks[dep_id].blocks if x != id_
                        ]
            t.updated_at = int(time.time())
            self._write_all(tasks)
        return True
