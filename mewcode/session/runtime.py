from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from ..context.session import SessionContext, new_session_context, open_session_context
from ..conversation.manager import ConversationManager
from ..provider.base import Message
from .recovery import recover_session_async
from .writer import SessionWriter


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

    def create_new(self) -> ConversationManager:
        self.close()
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
