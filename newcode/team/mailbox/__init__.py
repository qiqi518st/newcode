"""邮箱（ch15 F8）：Box——按 agentID 分文件存储 + 文件锁并发安全。

- 存储：`<team_config_dir>/mailbox/<agent_id>.json`，结构 `{"messages":[...]}`（F32）
- 两段式寻址：名称注册表（name → agent_id）→ 本 Box 按 agent_id 定位（F4.1）
- 写：抢 `<agent_id>.lock`（filelock.acquire）→ read-modify-write → `os.replace` 原子替换
- 广播：对除发件人外所有成员各 write 一次（F8.5）
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..filelock import acquire
from ..persistence import atomic_write_json
from .message import Message, MessageType

__all__ = ["Box", "Message", "MessageType"]


class Box:
    """按 agentID 分文件的邮箱（F8.2/F8.3），所有公开方法走文件锁。"""

    def __init__(self, dir_: str) -> None:
        self._dir = str(Path(dir_))
        Path(self._dir).mkdir(parents=True, exist_ok=True)

    def _path(self, agent_id: str) -> str:
        return str(Path(self._dir) / f"{agent_id}.json")

    def _lock_path(self, agent_id: str) -> str:
        return str(Path(self._dir) / f"{agent_id}.lock")

    async def write(self, agent_id: str, msg: Message) -> None:
        """追加一条消息（F8.4：锁内 read-modify-write + 原子替换）。"""
        if msg.timestamp == 0:
            msg.timestamp = int(time.time())
        async with acquire(self._lock_path(agent_id)):
            data: dict = {"messages": []}
            p = self._path(agent_id)
            if Path(p).exists():
                try:
                    data = json.loads(Path(p).read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    data = {"messages": []}
            messages = data.get("messages", [])
            if not isinstance(messages, list):
                messages = []
            messages.append(msg.to_dict())
            data["messages"] = messages
            atomic_write_json(p, data)

    async def write_broadcast(
        self, sender: str, msg: Message, member_ids: list[str]
    ) -> list[str]:
        """广播（F8.5）：对除发件人外所有成员各 write 一次；返回投递到的 agent_id 列表。"""
        delivered: list[str] = []
        for agent_id in member_ids:
            if agent_id == sender:
                continue
            await self.write(agent_id, msg)
            delivered.append(agent_id)
        return delivered

    async def read(self, agent_id: str) -> list[Message]:
        """读取全部消息（含已读）。"""
        p = self._path(agent_id)
        if not Path(p).exists():
            return []
        async with acquire(self._lock_path(agent_id)):
            try:
                data = json.loads(Path(p).read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return []
        messages = data.get("messages", []) if isinstance(data, dict) else []
        return [Message.from_dict(m) for m in messages if isinstance(m, dict)]

    async def read_unread(self, agent_id: str) -> tuple[list[int], list[Message]]:
        """读取未读消息：返回 (indices, messages)。"""
        indices: list[int] = []
        unread: list[Message] = []
        for i, m in enumerate(await self.read(agent_id)):
            if not m.read:
                indices.append(i)
                unread.append(m)
        return indices, unread

    async def mark_read(self, agent_id: str, indices: list[int]) -> None:
        """按 indices 批量标记已读（F11.1 读后标 read）。"""
        if not indices:
            return
        p = self._path(agent_id)
        if not Path(p).exists():
            return
        async with acquire(self._lock_path(agent_id)):
            try:
                data = json.loads(Path(p).read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return
            messages = data.get("messages", []) if isinstance(data, dict) else []
            if not isinstance(messages, list):
                return
            for i in indices:
                if 0 <= i < len(messages) and isinstance(messages[i], dict):
                    messages[i]["read"] = True
            data["messages"] = messages
            atomic_write_json(p, data)
