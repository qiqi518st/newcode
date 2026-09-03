"""执行后端抽象（ch15 F2/F12/F13）：Backend Protocol / SpawnRequest / new_backend。

- 三种后端 tmux / iterm2 / in-process 统一 `Backend` Protocol（spawn/wake/kill），
  屏蔽 spawn 差异（F2.2）
- `SpawnRequest.sub_agent/conv/task_mgr` 为 Any 可选字段——由调用方（team 包）预先
  构造好，backend 包只做调度、不反向依赖 agent（解环关键，TD-15）
- `new_backend` 工厂懒 import 各子模块，避免启动期全加载
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..types import BackendType


@dataclass
class SpawnRequest:
    """一次队员 spawn 的完整请求（F13）。"""

    team_name: str
    member_name: str
    agent_id: str  # 预生成，Pane 后端子进程直接拿（F3.2）
    worktree_path: str
    session_dir: str
    agent_type: str
    model: str
    initial_prompt: str
    plan_mode_required: bool = False

    # in-process 专用——同进程后端直接复用（由 team 包预先构造，backend 不关心类型）
    sub_agent: Any = None  # agent.Agent
    conv: Any = None  # conversation.ConversationManager
    task_mgr: Any = None  # task.Manager


@runtime_checkable
class Backend(Protocol):
    """执行后端统一抽象（F12）。"""

    def type(self) -> BackendType: ...

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:  # (pane_id, agent_id)
        ...

    async def wake(self, pane_id: str, agent_id: str) -> None: ...

    async def kill(self, pane_id: str, agent_id: str) -> None: ...


def new_backend(t: BackendType, **deps) -> Backend:
    """按类型构造后端实例（懒 import 子模块，TD-15）。"""
    if t == BackendType.TMUX:
        from .tmux import TmuxBackend

        return TmuxBackend(**deps)
    if t == BackendType.ITERM2:
        from .iterm2 import Iterm2Backend

        return Iterm2Backend(**deps)
    if t == BackendType.IN_PROCESS:
        from .inprocess import InProcessBackend

        return InProcessBackend(**deps)
    raise ValueError(f"未知后端类型: {t}")
