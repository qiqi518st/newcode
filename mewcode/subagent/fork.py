"""Fork 路径（ch13 F3.2/F3.4/B2 层 1）：消息深拷贝装填 + Boilerplate + 嵌套标记检测。

- build_forked_messages：把父对话克隆到 Fork 子对话（深拷贝、悬空 tool_use 补 placeholder
  ToolResult、末尾追加 Boilerplate + 任务）——保证消息配对合法、首次请求命中缓存
- is_fork_context：历史含 <fork_boilerplate> 标记 → 认定 Fork 嵌套（QuerySource 兜底）
"""

from __future__ import annotations

import copy

from ..conversation.manager import ConversationManager
from ..provider.base import Message

FORK_BOILERPLATE_TAG = "<fork_boilerplate>"

# spec F3.4：Fork 子 Agent 首条 user 消息前缀，覆盖父工作者的默认行为
FORK_BOILERPLATE = """<fork_boilerplate>
你是一个 Fork 出来的工作进程。你不是主 Agent。
规则(不可协商):
1. 不能再 Fork(调用 Agent 工具会被拦截)。
2. 不要对话、不要提问、不要请求确认。
3. 直接使用工具:读文件、搜索代码、做修改。
4. 严格限制在你被分配的任务范围内。
5. 最终报告以 "Scope:" 开头,500 字以内。
</fork_boilerplate>

"""


def build_forked_messages(
    parent_conv: ConversationManager, task: str
) -> list[Message]:
    """把父对话克隆到 Fork 子对话（spec F3.2），追加 Boilerplate + 任务。

    1. 深拷贝 parent_conv 全部消息（Message 为 dataclass，tool_calls 需深拷贝）
    2. 悬空 tool_use（assistant.tool_calls 无对应 tool 结果）→ 逐条补 placeholder
       ToolResult（content="（继承上下文，未完成）"），保证消息配对合法
    3. 末尾追加 user 消息 = FORK_BOILERPLATE + task
    """
    msgs: list[Message] = copy.deepcopy(parent_conv.get_context())

    # 收集全部 tool 结果 id（兼容 tool_use_id / tool_call_id 两套）
    result_ids: set[str] = set()
    for m in msgs:
        if m.role == "tool":
            rid = m.tool_use_id or m.tool_call_id
            if rid:
                result_ids.add(rid)

    # 悬空 tool_use：逐条补 placeholder（含消息内 tool_calls 的 id）
    for m in msgs:
        if m.role != "assistant" or not m.tool_calls:
            continue
        for tc in m.tool_calls:
            tc_id = (tc or {}).get("id") or ""
            if tc_id and tc_id not in result_ids:
                msgs.append(
                    Message(
                        role="tool",
                        content="（继承上下文，未完成）",
                        tool_use_id=tc_id,
                        tool_call_id=tc_id,
                        name="",
                    )
                )

    msgs.append(Message(role="user", content=FORK_BOILERPLATE + task))
    return msgs


def is_fork_context(msgs: list[Message]) -> bool:
    """判定对话历史是否来自 Fork：任意 user 消息含 <fork_boilerplate> 标记。

    B2 层 1 兜底检测——工具列表残留 Agent 工具时，靠它拦截 Fork 嵌套（spec F6.5）。
    """
    for m in msgs:
        if m.role == "user" and FORK_BOILERPLATE_TAG in m.content:
            return True
    return False
