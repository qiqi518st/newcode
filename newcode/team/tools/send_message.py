"""SendMessage 工具（ch15 F31/F34/F7.7-F7.8）：队员间/Lead 点对点 + 广播。

- 寻址：name / agent_id / `*` 广播（F7.7）；经名称注册表两段式解析（F4.1）
- 写邮箱（统一真值）→ Pane 后端 wake → in-process 已停目标续派（TD-8 去重）
- 结构化消息权限：plan_approval_response 仅 Lead 可发；shutdown_response 只能发给 Lead（F8.6）
"""

from __future__ import annotations

import time

from ...agent.team_hook import current_teammate
from ...provider.base import ToolResult
from ..backend import new_backend
from ..mailbox import Box, Message, MessageType
from ..types import SendMessageValidationError, TeamError
from . import _resolve_team, _team_error, _team_json

_VALID_TYPES = {
    MessageType.TEXT,
    MessageType.SHUTDOWN_REQUEST,
    MessageType.SHUTDOWN_RESPONSE,
    MessageType.PLAN_APPROVAL_RESPONSE,
}


class TeamSendMessageTool:
    """SendMessage：经 Mailbox 给队员/Lead 发消息（F31）。"""

    def __init__(self, mgr) -> None:
        self._mgr = mgr

    @property
    def name(self) -> str:
        return "SendMessage"

    @property
    def description(self) -> str:
        return "给团队成员发消息（name/agent_id/* 广播；text/shutdown/plan_approval 结构化）"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "队员名 / agent_id / *（广播，必填）",
                },
                "summary": {
                    "type": "string",
                    "description": "纯文本消息必带 5-10 词摘要",
                },
                "message": {"type": "string", "description": "纯文本消息体"},
                "type": {
                    "type": "string",
                    "enum": [
                        "text",
                        "shutdown_request",
                        "shutdown_response",
                        "plan_approval_response",
                    ],
                    "description": "消息类型（默认 text）",
                },
                "payload": {
                    "type": "object",
                    "description": "结构化消息载荷（如 plan_approval 的 {approve, feedback}）",
                },
            },
            "required": ["to"],
        }

    @property
    def read_only(self) -> bool:
        return False

    @property
    def is_system(self) -> bool:
        return False

    async def execute(self, arguments: dict) -> ToolResult:
        team = _resolve_team(self._mgr)
        if team is None:
            return _team_error("当前无团队上下文")
        to = str(arguments.get("to") or "").strip()
        if not to:
            return _team_error("to 必填")
        msg_type = MessageType(str(arguments.get("type") or MessageType.TEXT.value))
        if msg_type not in _VALID_TYPES:
            return _team_error(f"非法消息类型: {msg_type.value}")
        me = current_teammate()
        sender = me.member_name if me else team.lead_agent_id
        sender_id = me.agent_id if me else team.lead_agent_id

        # 结构化消息权限（F8.6）
        try:
            self._check_type_permissions(msg_type, me)
        except SendMessageValidationError as exc:
            return _team_error(str(exc))

        msg = Message(
            from_=sender,
            to=to,
            type=msg_type,
            summary=str(arguments.get("summary") or ""),
            content=str(arguments.get("message") or ""),
            payload=arguments.get("payload"),
        )

        box = Box(team.mailbox_dir)
        delivered: list[str] = []
        try:
            if to == "*":
                member_ids = [m.agent_id for m in team.members if m.name != "lead"]
                delivered = await box.write_broadcast(sender_id, msg, member_ids)
            else:
                target_id = self._mgr.registry.resolve(to)
                if target_id is None:
                    return _team_error(f"无法解析目标: {to}")
                await box.write(target_id, msg)
                delivered = [target_id]
                await self._after_deliver(team, target_id, msg)
        except TeamError as exc:
            return _team_error(str(exc))
        return _team_json(
            {"delivered_to": delivered, "timestamp": msg.timestamp or int(time.time())}
        )

    def _check_type_permissions(self, msg_type: MessageType, me) -> None:
        """F8.6：plan_approval_response 仅 Lead；shutdown_response 只能发给 Lead（发送侧校验）。"""
        if msg_type == MessageType.PLAN_APPROVAL_RESPONSE and me is not None:
            raise SendMessageValidationError("plan_approval_response 仅 Lead 可发送")

    async def _after_deliver(self, team, target_id: str, msg: Message) -> None:
        """写邮箱后的投递动作（F8.7/F12.2/TD-8）：Pane wake / in-process 已停续派。"""
        member = team.member_by_agent_id(target_id)
        if member is None:
            return
        if member.backend_type.value != "in-process":
            # Pane 后端：wake 目标窗格
            backend = new_backend(member.backend_type, task_mgr=self._mgr.task_mgr)
            await backend.wake(member.pane_id, target_id)
            return
        # in-process：已停则续派（mark_read 刚写消息防重复，TD-8）
        bt = self._mgr.task_mgr.get(target_id) if self._mgr.task_mgr else None
        if bt is None:
            return
        if bt.status.name == "RUNNING":
            return  # 运行中：下一轮 Loop 读 mailbox 自然拿到
        box = Box(team.mailbox_dir)
        indices, _unread = await box.read_unread(target_id)
        await box.mark_read(target_id, indices)
        try:
            await self._mgr.set_member_active(team, member.name, True)
            self._mgr.task_mgr.continue_agent(target_id, msg.content or msg.summary)
        except Exception as exc:  # noqa: BLE001 —— 续派失败转结构化错误
            raise SendMessageValidationError(f"续派失败: {exc}")
