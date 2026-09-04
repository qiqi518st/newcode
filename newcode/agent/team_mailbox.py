"""队员 Loop 头部邮箱注入（ch15 F11.1/F42 + F13.4/TD-合并）。

- `inject_incoming(agent, teammate)`：agent.run 每轮调 LLM 前调用——读未读 →
  组 `<incoming-messages>` reminder（Message 对象，供 assemble）→ mark_read
- Plan 审批集中处理（双后端统一）：`plan_approval_response(approve=True)` →
  `teammate.set_permission("default")` + reminder 注明；approve=False → 附 feedback
- `shutdown_request`：提示队员可自主选择回复（LLM 决策不强制，F43）
- 本模块在 agent 包内，只依赖 agent/team_hook 的 TeammateContext 闭包——不 import
  team/mailbox（TD-12 闭包解环）
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..provider.base import Message

if TYPE_CHECKING:
    from .team_hook import TeammateContext

_CONTENT_PREVIEW_CHARS = 200
_PLAN_MODE_NAME = "default"


def _format_incoming(teammate: TeammateContext, msgs: list) -> str:
    """组 `<incoming-messages>` 文本（F42）。"""
    lines = [f"收到 {len(msgs)} 条新消息:"]
    for i, m in enumerate(msgs, 1):
        ts = (
            time.strftime("%H:%M:%S", time.localtime(m.timestamp))
            if m.timestamp
            else "-"
        )
        content = (m.content or "").replace("\n", " ")[:_CONTENT_PREVIEW_CHARS]
        lines.append(
            f"[{i}] 来自 {m.from_}(type={m.type},ts={ts}): {m.summary}"
            + (f"\n    {content}" if content else "")
        )
    return "\n".join(lines)


def _plan_decision_note(msgs: list) -> str | None:
    """Plan 审批切换（F13.4，集中到注入处理）：返回附加文案；调用方执行 set_permission。"""
    for m in msgs:
        if getattr(m, "type", "") != "plan_approval_response":
            continue
        payload = m.payload or {}
        if payload.get("approve"):
            return "Lead 已批准你的计划，权限已切换到执行模式，可执行计划。"
        feedback = str(payload.get("feedback", ""))
        return f"Lead 驳回了你的计划，反馈：{feedback}。请调整后重新提交。"
    return None


async def inject_incoming(agent: object, teammate: TeammateContext) -> list[Message]:
    """读未读 → `<incoming-messages>` reminder；处理 plan 审批切换；mark_read（F11.1）。

    agent 用于 set_permission（经 teammate.set_permission 闭包回调，避免 agent 直接
    依赖 permission 包的具体类）。返回 reminders（Message 列表）。
    """
    indices, msgs = await teammate.read_unread()
    if not msgs:
        return []
    notes: list[str] = []
    note = _plan_decision_note(msgs)
    if note is not None:
        if teammate.set_permission is not None:
            teammate.set_permission(_PLAN_MODE_NAME)
        notes.append(note)
    for m in msgs:
        if getattr(m, "type", "") == "shutdown_request":
            notes.append(
                "收到 shutdown_request：可自主选择 SendMessage 回复 shutdown_response "
                "（approve=True 停止 / approve=False 拒绝并附理由）。"
            )
    body = _format_incoming(teammate, msgs)
    if notes:
        body += "\n\n" + "\n".join(notes)
    await teammate.mark_read(indices)
    return [
        Message(role="user", content=f"<incoming-messages>{body}</incoming-messages>")
    ]
