"""对话上下文管理器：维护消息列表，实现滑动窗口"""

from ..provider.base import Message
from ..prompt.resources import SYSTEM_PROMPT


class ConversationManager:
    """对话上下文管理器，维护消息列表并实现滑动窗口"""

    def __init__(self, system_prompt: str, max_turns: int) -> None:
        self._system_prompt = system_prompt if system_prompt else SYSTEM_PROMPT
        self._max_turns = max_turns
        self._messages: list[Message] = []

    def add_user(self, content: str) -> None:
        """追加用户消息"""
        self._messages.append(Message(role="user", content=content))

    def add_assistant(self, content: str) -> None:
        """追加助手消息，并触发滑动窗口裁剪"""
        self._messages.append(Message(role="assistant", content=content))
        self._trim()

    def get_context(self) -> list[Message]:
        """返回完整上下文: system prompt + 窗口内消息"""
        system_msg = Message(role="system", content=self._system_prompt)
        return [system_msg] + list(self._messages)

    def _trim(self) -> None:
        """超出 max_turns 时，丢弃最早的一对 user/assistant"""
        # 统计 user/assistant 消息对数
        pairs = len(self._messages) // 2
        if pairs > self._max_turns:
            excess = (pairs - self._max_turns) * 2
            self._messages = self._messages[excess:]