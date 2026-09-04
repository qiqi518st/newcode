"""替换决策账本 ContentReplacementState（spec F5：决策冻结，PromptCache 稳定）。"""

from collections.abc import Callable
from typing import Literal

Decision = Literal["replaced", "kept", "unseen"]


class ContentReplacementState:
    """第一层工具结果替换决策账本（会话级）。

    无需显式锁——Python asyncio 单线程事件循环保证串行：decide_once 是一次性
    完成「查账本 → 决策 → 写账本」的临界区入口，避免出现「已 Seen 但 replacement
    未写」的中间态（N2）。
    """

    def __init__(self) -> None:
        self._seen_ids: set[str] = set()
        self._replacements: dict[str, str] = {}

    def decide_once(
        self,
        tool_use_id: str,
        original_content: str,
        decide: Callable[[], tuple[str, str]],
    ) -> str:
        """持锁完成"查账本 → 决策 → 写账本"原子操作。

        decide 回调返回 (decision, preview)：
          - ("kept", _)     → 写 _seen_ids，不写 _replacements；返回原 content。
          - ("replaced", p) → 写 _seen_ids + _replacements；返回 preview。
          - ("skip", _)     → 都不写；返回原 content（下轮重试，F5b）。
        若 id 已 Seen：直接返回账本存量结果（kept → 原 content；
        replaced → _replacements[id] 复用冻结字符串，不重造，F5d）。
        """
        if tool_use_id in self._seen_ids:
            return self._replacements.get(tool_use_id, original_content)

        decision, preview = decide()
        if decision == "kept":
            self._seen_ids.add(tool_use_id)
            return original_content
        if decision == "replaced":
            self._seen_ids.add(tool_use_id)
            self._replacements[tool_use_id] = preview
            return preview
        # skip：不写账本，下轮重评
        return original_content

    def decision_for(self, tool_use_id: str) -> tuple[Decision, str | None]:
        """只读查询：replaced 带冻结预览 / kept 无预览 / unseen 未决策。"""
        if tool_use_id in self._seen_ids:
            preview = self._replacements.get(tool_use_id)
            if preview is not None:
                return ("replaced", preview)
            return ("kept", None)
        return ("unseen", None)
