"""对话上下文管理器：维护消息列表，实现滑动窗口"""

from ..provider.base import Message, ToolCall, ToolResult
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

    def add_tool_call(self, tool_call: ToolCall) -> None:
        """追加 assistant 的工具调用回合（保存结构化声明）"""
        self._messages.append(
            Message(
                role="assistant",
                content="",
                tool_calls=[{
                    "id": tool_call.tool_use_id or tool_call.tool_call_id or "",
                    "name": tool_call.tool_name,
                    "arguments": tool_call.arguments,
                }],
                tool_call_id=tool_call.tool_call_id,
                tool_use_id=tool_call.tool_use_id,
                name=tool_call.tool_name,
            )
        )

    def add_tool_result(self, result: ToolResult) -> None:
        """追加 tool 角色的结果消息"""
        content = result.output if result.status == "ok" else result.error
        # 从上一个 assistant tool_call 消息中提取 ID / name
        tool_call_id = None
        tool_use_id = None
        name = None
        if self._messages and self._messages[-1].role == "assistant":
            tool_call_id = self._messages[-1].tool_call_id
            tool_use_id = self._messages[-1].tool_use_id
            name = self._messages[-1].name

        self._messages.append(
            Message(
                role="tool",
                content=content,
                tool_call_id=tool_call_id,
                tool_use_id=tool_use_id,
                name=name,
            )
        )

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