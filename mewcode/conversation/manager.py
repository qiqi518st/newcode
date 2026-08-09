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

    def last_role(self) -> str | None:
        """返回最后一条消息的 role，空列表时返回 None"""
        return self._messages[-1].role if self._messages else None

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

    def add_assistant_with_tool_calls(
        self, text: str, tool_calls: list[ToolCall]
    ) -> None:
        """追加 assistant 消息，同时含文本和多个工具调用声明"""
        self._messages.append(
            Message(
                role="assistant",
                content=text,
                tool_calls=[
                    {
                        "id": tc.tool_use_id or tc.tool_call_id or "",
                        "name": tc.tool_name,
                        "arguments": tc.arguments,
                    }
                    for tc in tool_calls
                ],
            )
        )

    def add_tool_results(
        self, results: list[tuple[ToolCall, ToolResult]]
    ) -> None:
        """按序追加 tool 结果消息"""
        for tc, tr in results:
            content = tr.output if tr.status == "ok" else tr.error
            self._messages.append(
                Message(
                    role="tool",
                    content=content,
                    tool_call_id=tc.tool_call_id,
                    tool_use_id=tc.tool_use_id,
                    name=tc.tool_name,
                )
            )

    def add_cancelled_tool_result(self, tool_call: ToolCall) -> None:
        """为未完成的工具调用补「已取消」结果，确保配对完整"""
        self._messages.append(
            Message(
                role="tool",
                content="已取消",
                tool_call_id=tool_call.tool_call_id,
                tool_use_id=tool_call.tool_use_id,
                name=tool_call.tool_name,
            )
        )

    def add_tool_result(self, tool_call: ToolCall, result: ToolResult) -> None:
        """追加 tool 角色的结果消息（与对应 tool_call 配对，确保 ID 一致）

        注意：必须显式传 tool_call——assistant 消息含多个 tool_calls 时，
        无法从消息对象推断结果属于哪个调用，若 tool_use_id 为空，
        API 会报 'tool_call_ids did not have response messages'。
        """
        content = result.output if result.status == "ok" else result.error
        self._messages.append(
            Message(
                role="tool",
                content=content,
                tool_call_id=tool_call.tool_call_id,
                tool_use_id=tool_call.tool_use_id,
                name=tool_call.tool_name,
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