"""Agent 名称注册表（ch15 F9）：name ↔ agent_id 双向映射。

- 两段式寻址的基础（F4.1）：SendMessage 先 resolve(name_or_id) → agent_id
- 后注册覆盖前（弱引用语义，F9.4）；同名指向同一 agent_id 时先反向清理旧映射
- 统一这套 registry，`task.Manager` 的 `_by_name` 委托给它（plan T20）
"""

from __future__ import annotations

import threading


class AgentNameRegistry:
    """name → agent_id 双向映射（threading.Lock 保护，F9.1）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_name: dict[str, str] = {}  # name → agent_id
        self._by_id: dict[str, str] = {}  # agent_id → name

    def register(self, name: str, agent_id: str) -> None:
        """注册映射；后注册覆盖前（F9.4）；agent_id 换名时清理旧反向映射。"""
        with self._lock:
            old_id = self._by_name.get(name)
            if old_id is not None and old_id != agent_id:
                self._by_id.pop(old_id, None)
            old_name = self._by_id.get(agent_id)
            if old_name is not None and old_name != name:
                self._by_name.pop(old_name, None)
            self._by_name[name] = agent_id
            self._by_id[agent_id] = name

    def unregister(self, name: str) -> None:
        """按 name 移除映射。"""
        with self._lock:
            agent_id = self._by_name.pop(name, None)
            if agent_id is not None:
                self._by_id.pop(agent_id, None)

    def unregister_by_agent_id(self, agent_id: str) -> None:
        """按 agent_id 移除映射。"""
        with self._lock:
            name = self._by_id.pop(agent_id, None)
            if name is not None:
                self._by_name.pop(name, None)

    def resolve(self, name_or_id: str) -> str | None:
        """解析为 agent_id（F9.2）：name → agent_id；已是 agent_id 则原样返回。"""
        with self._lock:
            if name_or_id in self._by_name:
                return self._by_name[name_or_id]
            if name_or_id in self._by_id:
                return name_or_id
            return None

    def name_of(self, agent_id: str) -> str | None:
        """agent_id → name 反查。"""
        with self._lock:
            return self._by_id.get(agent_id)

    def list_(self) -> dict[str, str]:
        """当前 name → agent_id 快照。"""
        with self._lock:
            return dict(self._by_name)
