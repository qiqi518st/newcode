from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ..context.session import SessionContext, new_session_context, open_session_context
from ..conversation.manager import ConversationManager
from ..provider.base import Message
from .recovery import recover_session_async
from .writer import SessionWriter

if TYPE_CHECKING:
    from ..hooks.engine import Engine


class SessionRuntime:
    def __init__(
        self,
        workspace: str | Path,
        max_turns: int = 50,
        model: str | None = None,
        context_window: int | None = None,
        reserve: int = 0,
        compressor: Callable[[list[Message]], Awaitable[list[Message]]] | None = None,
    ):
        self.workspace = Path(workspace)
        self.max_turns = max_turns
        self.model = model
        self._context_window = context_window
        self._reserve = reserve
        self._compressor = compressor
        self.context: SessionContext | None = None
        self.writer: SessionWriter | None = None
        self.conversation: ConversationManager | None = None

        # ch12：Hook prompt 注入的 reminder 队列（F5.6/F8.3）——
        # 与 plan reminder 同注入点、会话生命周期内重置（/clear、/resume、/session_new）。
        self.pending_reminders: list[str] = []
        self._reminders_lock = threading.Lock()
        # Hook 引擎（TUI/main 装配时设置；None = 未启用，重置 once 时跳过）
        self.hook_engine: Engine | None = None

    @property
    def session_id(self) -> str | None:
        return self.context.session_id if self.context else None

    @property
    def session_dir(self) -> Path | None:
        return (
            Path(self.context.session_dir)
            if self.context and self.context.session_dir
            else None
        )

    # ── ch12：Hook prompt reminder 队列（F5.6/F8.3）─────────────────

    def append_reminders(self, prompts: list[str]) -> None:
        """追加待注入的 hook prompt（线程安全；无内容直接跳过）。"""
        if not prompts:
            return
        with self._reminders_lock:
            self.pending_reminders.extend(prompts)

    def take_reminders(self) -> list[str]:
        """取出并清空待注入的 hook prompt（线程安全）。"""
        with self._reminders_lock:
            prompts = list(self.pending_reminders)
            self.pending_reminders = []
            return prompts

    def _clear_reminders(self) -> None:
        """清空 reminder 队列（新会话重置，与 ActiveSkills 同生命周期 N8）。"""
        with self._reminders_lock:
            self.pending_reminders = []

    async def reset_for_new_session(self) -> None:
        """集中重置点（/clear、/resume、/session_new 调用，调用方只调这一个）：
        清空 pending_reminders + 调 hook_engine.reset_for_new_session() 清 once（F2.2/N8）。"""
        self._clear_reminders()
        if self.hook_engine is not None:
            await self.hook_engine.reset_for_new_session()

    def create_new(self) -> ConversationManager:
        self.close()
        self._clear_reminders()
        self.context = new_session_context(str(self.workspace))
        self.writer = SessionWriter(self.context.session_dir, model=self.model)
        self.conversation = ConversationManager(
            self.max_turns,
            on_append=self.writer.append_message,
            on_replace=lambda messages: (
                self.writer.write_compact_marker(),
                self.writer.append_all(messages),
            ),
        )
        return self.conversation

    async def resume(self, session_id: str) -> ConversationManager:
        context = open_session_context(str(self.workspace), session_id)
        recovered = await recover_session_async(
            context.session_dir,
            context_window=self._context_window,
            reserve=self._reserve,
            compressor=self._compressor,
        )
        self.close()
        self._clear_reminders()
        self.context = context
        self.writer = SessionWriter.open_existing(context.session_dir, model=self.model)
        self.conversation = ConversationManager(
            self.max_turns,
            messages=recovered.messages,
            on_append=self.writer.append_message,
            on_replace=lambda messages: (
                self.writer.write_compact_marker(),
                self.writer.append_all(messages),
            ),
        )
        return self.conversation

    def close(self) -> None:
        if self.writer:
            self.writer.close()
