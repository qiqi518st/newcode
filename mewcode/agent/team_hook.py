"""Team 集成 Protocol（ch15 TD-12/TD-14）：agent 包与 team 包解耦的契约层。

- `TeamHook`：AgentTool 委托 team spawn 的接口（agent 包不 import team 包；
  实现由 cli 装配时注入，team 包以结构类型匹配）
- `TeamSpawnRequest`：Agent 工具 team_name 分支的参数载体
- `IncomingMessage`：成员邮箱消息的轻量投影（独立于 team.mailbox.Message，避免依赖）
- `TeammateContext`：成员侧上下文，**持闭包**（read_unread/mark_read/set_permission）
  而非 Box 引用——agent 包对协作层零依赖（TD-12 闭包注入）
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class TeamSpawnRequest:
    """Agent 工具 team_name 分支 → TeamHook.spawn_teammate 的参数（F10/F25）。"""

    team_name: str
    prompt: str
    subagent_type: str = ""
    model: str = ""
    name: str = ""  # 队员名（缺省由 spawn 生成）
    plan_mode_required: bool = False


class TeamHook(Protocol):
    """team 包实现的结构类型（TD-12）。"""

    async def spawn_teammate(self, req: TeamSpawnRequest) -> str:
        """spawn 一个队员；返回 final_text（立即返回 task_id JSON 描述）。"""
        ...

    def is_teammate_context(self, ctx: Any) -> tuple[str, str, bool]:
        """当前上下文是否在某队员执行上下文中（嵌套 spawn 拦截，TD-14）。

        返回 (team_name, member_name, is_inprocess)；非队员上下文返回 ("", "", False)。
        """
        ...


@dataclass
class IncomingMessage:
    """成员邮箱消息的轻量投影（仅 agent 包需要的字段，F11.2）。"""

    from_: str
    type: str
    summary: str
    content: str
    payload: dict[str, Any] | None = None
    timestamp: int = 0


@dataclass
class TeammateContext:
    """成员侧上下文：持闭包注入（TD-12），agent 包不 import team/mailbox。"""

    team_name: str
    member_name: str
    agent_id: str
    backend_type: str = (
        "in-process"  # tmux/iterm2/in-process（嵌套 spawn 拦截用，TD-14）
    )
    read_unread: Callable[[], Awaitable[tuple[list[int], list[IncomingMessage]]]] = (
        field(default_factory=lambda: _empty_read_unread)
    )
    mark_read: Callable[[list[int]], Awaitable[None]] = field(
        default_factory=lambda: _noop
    )
    set_permission: Callable[[str], None] | None = None  # Plan 审批切换（F13.4）
    get_permission: Callable[[], str] | None = (
        None  # 取 Lead 当前模式（本期固定 default）
    )


# 当前执行上下文是否在某个队员内（嵌套 spawn 拦截，TD-14）。
# agent.run() 在成员启动时 set；AgentTool.execute 读取判断调用者身份。
_CURRENT_TEAMMATE: contextvars.ContextVar[TeammateContext | None] = (
    contextvars.ContextVar("current_teammate", default=None)
)


def current_teammate() -> TeammateContext | None:
    """读取当前任务上下文中的 TeammateContext（非成员上下文返回 None）。"""
    return _CURRENT_TEAMMATE.get()


def set_current_teammate(tc: TeammateContext | None) -> None:
    """成员 Agent 启动时注入当前上下文（agent.run 调用）。"""
    _CURRENT_TEAMMATE.set(tc)


async def _empty_read_unread() -> tuple[list[int], list[IncomingMessage]]:
    return [], []


async def _noop(_indices: list[int]) -> None:
    pass
