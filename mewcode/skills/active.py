"""单会话激活状态（F5.1）：ActiveSkills 容器。

记录当前会话激活的 Skill（保持激活顺序，重复激活覆盖原位置）。env 每轮由 Agent
经 adapter 转成 prompt 条目；压缩时由 ContextManager 调 enforce_budget 做预算淘汰（F8.1）。

接口对齐 ch08 SkillRegistry 骨架：total_tokens(estimator) 保留（N10，压缩预算用）。
"""

from __future__ import annotations

import threading

from ..skills.constants import ACTIVE_SKILL_TOKEN_BUDGET
from ..skills.types import ActiveEntry


class ActiveSkills:
    """激活 Skill 状态容器（会话级，线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[ActiveEntry] = []  # 保持激活顺序
        self._index: dict[str, int] = {}  # 名字 → _entries 位置（重复激活覆盖原位置）

    def activate(self, name: str, body: str) -> None:
        """激活/重复激活一个 Skill。

        重复激活时覆盖原位置的 body（不改变顺序，F5.1）。
        """
        with self._lock:
            if name in self._index:
                idx = self._index[name]
                self._entries[idx] = ActiveEntry(name=name, body=body)
                return
            self._index[name] = len(self._entries)
            self._entries.append(ActiveEntry(name=name, body=body))

    def deactivate(self, name: str) -> None:
        """失活单个 Skill（F8.3 手动移除）。"""
        with self._lock:
            idx = self._index.pop(name, None)
            if idx is None:
                return
            self._entries.pop(idx)
            # 重排后续索引
            for i in range(idx, len(self._entries)):
                self._index[self._entries[i].name] = i

    def clear(self) -> None:
        """清空全部（/clear 与 /session_new 调用，F5.5/F8.2）。"""
        with self._lock:
            self._entries = []
            self._index = {}

    def snapshot(self) -> list[ActiveEntry]:
        """返回激活条目拷贝（env 装配用，防外部改动）。"""
        with self._lock:
            return list(self._entries)

    def names(self) -> list[str]:
        """当前激活 Skill 名（按激活顺序）。"""
        with self._lock:
            return [e.name for e in self._entries]

    def total_tokens(self, estimator) -> int:
        """按估算器求激活 Skill 总 token（N10 兼容 ch08 SkillRegistry 接口）。"""
        with self._lock:
            total = 0
            for e in self._entries:
                try:
                    total += estimator(e.body)
                except TypeError:
                    total += estimator(e.body, e.body)  # 兼容双参估算器形态
            return total

    def enforce_budget(
        self, budget: int = ACTIVE_SKILL_TOKEN_BUDGET
    ) -> list[ActiveEntry]:
        """压缩预算淘汰（F8.1）：按激活顺序淘汰最旧，直至总 token ≤ budget。

        返回幸存列表（拷贝）；被淘汰的条目从容器移除。预算估算用
        固定 char/token 比例（ESTIMATE_CHARS_PER_TOKEN 语义，取整）。
        """
        with self._lock:
            estimator = _char_estimator
            total = self.total_tokens(estimator)
            survivors = list(self._entries)
            while survivors and total > budget:
                removed = survivors.pop(0)  # 最旧先踢（F8.1）
                total -= estimator(removed.body)
                self._index.pop(removed.name, None)
            self._entries = survivors
            for i, e in enumerate(survivors):
                self._index[e.name] = i
            return list(survivors)


def _char_estimator(text: str) -> int:
    """字符/token 近似（与 context.tokens 的 ESTIMATE_CHARS_PER_TOKEN 一致口径）。"""
    return max(1, len(text) // 4)
