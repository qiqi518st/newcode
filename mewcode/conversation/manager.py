"""对话上下文管理器：维护消息列表，实现滑动窗口（ch08：裁剪改按 user 分组整组丢，保配对）"""

from ..context.dropper import MessageGroupDropper
from ..provider.base import Message, ToolCall, ToolResult

# 降级条数兜底倍数：仅当组数超过 max_turns * 2 才触发裁剪（主裁剪权已交 context，
# 本裁剪是防止 context 未接入/失败时的无界增长兜底，语义从「每轮对」放宽为「每组」）
_TRIM_GROUP_CAP_MULTIPLIER = 2


class ConversationManager:
    """对话上下文管理器，维护消息列表并实现滑动窗口"""

    def __init__(self, max_turns: int) -> None:
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
                tool_calls=[
                    {
                        "id": tool_call.tool_use_id or tool_call.tool_call_id or "",
                        "name": tool_call.tool_name,
                        "arguments": tool_call.arguments,
                    }
                ],
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

    def add_tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
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
        """返回窗口内会话历史（不含 system；稳定系统提示由组装管线负责）"""
        return list(self._messages)

    def get_messages_ref(self) -> list[Message]:
        """返回 self._messages **原始引用**（非副本），供 offload_and_snip 就地改写 content。

        注意：调用方就地改写会污染内部状态——这是刻意设计（ch08 L1 替换）；
        get_context 仍返回副本不变（Agent assemble 用副本，副本取自压缩后的原始列表）。
        """
        return self._messages

    def replace_history(self, new_messages: list[Message]) -> None:
        """整段替换历史（第二层摘要成功后的新消息列表，spec F15）。"""
        self._messages = list(new_messages)

    def _trim(self) -> None:
        """超出 max_turns 时，从头部按「user 分界的组」整组丢弃（不拆 tool_use/tool_result 对）。

        ch08 语义变更：原实现按 user/assistant 对整对丢弃，会切开工具回合；现按
        MessageGroupDropper.group_by_user 分组、整组丢，天然保对。触发阈值放宽到
        max_turns * _TRIM_GROUP_CAP_MULTIPLIER（降级条数兜底，主裁剪权已交 context）。
        """
        groups = MessageGroupDropper.group_by_user(self._messages)
        cap = self._max_turns * _TRIM_GROUP_CAP_MULTIPLIER
        if len(groups) > cap:
            keep = groups[len(groups) - cap :]
            self._messages = [m for g in keep for m in g]
