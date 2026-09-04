"""in-process 后端（ch15 F5/F18）：同进程 asyncio task 轻量运行。

- spawn：复用 `task.Manager.launch`（F5.1）——创建带 cwd=worktree 的子 Agent，
  在 asyncio task 里跑 run_to_completion；返回 (pane_id="", agent_id=task_id)
- wake：no-op（同进程，下一轮 Loop 自动读邮箱，F5.2）
- kill：`task.Manager.stop(agent_id)`（F5.3）
- 本模块允许依赖 agent/task/conversation（低层，TD-15）；`req.sub_agent` 由 team 包
  预先构造（含 cwd=worktree_path / dont_ask=True / teammate 注入）
"""

from __future__ import annotations

from ..types import BackendType
from . import SpawnRequest


class InProcessBackend:
    """in-process 后端（F18）。"""

    def __init__(self, task_mgr=None, **_deps) -> None:
        self._task_mgr = task_mgr

    def type(self) -> BackendType:
        return BackendType.IN_PROCESS

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        """复用 task.Manager.launch 起 asyncio task（F5.1）；返回 ("", task_id)。"""
        if self._task_mgr is None:
            raise RuntimeError("in-process 后端缺少 task_mgr")
        task_id = self._task_mgr.launch(
            req.sub_agent, req.initial_prompt, name=req.member_name
        )
        return "", task_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        """no-op（F5.2）：同进程，下一轮 Loop 自动读邮箱。"""

    async def kill(self, pane_id: str, agent_id: str) -> None:
        """终止 asyncio task（F5.3）。"""
        if self._task_mgr is not None:
            self._task_mgr.stop(agent_id)
