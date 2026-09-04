"""iTerm2 后端（ch15 F4/F17）——骨架实现，待人工验证。

本环境（WSL/Linux）无法验证 macOS 专属能力；接口按 `it2` CLI 约定实现：
- spawn：`it2 split --new-pane --command "<cmd>"`（cmd 含预生成 --agent-id）
- wake：`it2 send-text --pane <pane_id> ""`
- kill：`it2 close-pane --pane <pane_id>`

**待人工验证**：macOS + iTerm2 + it2 实机补验命令形态（checklist 待人工验证节）。
"""

from __future__ import annotations

import asyncio
import shlex

from ..types import BackendType, BackendUnavailableError
from . import SpawnRequest
from .tmux import build_member_cmd


class Iterm2Backend:
    """iTerm2 后端（F17，骨架）。"""

    def __init__(self, **_deps) -> None:
        self._deps = _deps

    def type(self) -> BackendType:
        return BackendType.ITERM2

    async def _run(self, args: list[str]) -> str:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            raise BackendUnavailableError("it2 命令超时")
        if proc.returncode != 0:
            raise BackendUnavailableError(
                f"it2 命令失败（rc={proc.returncode}）: "
                f"{stderr.decode('utf-8', 'replace').strip()}"
            )
        return stdout.decode("utf-8", "replace").strip()

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        """`it2 split --new-pane --command "<cmd>"`；输出解析 pane id。"""
        cmd = " ".join(shlex.quote(c) for c in build_member_cmd(req))
        out = await self._run(["it2", "split", "--new-pane", "--command", cmd])
        pane_id = out  # it2 输出约定为 pane id（实机待验证）
        if not pane_id:
            raise BackendUnavailableError("it2 split 未返回 pane id")
        return pane_id, req.agent_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        if not pane_id:
            return
        await self._run(["it2", "send-text", "--pane", pane_id, ""])

    async def kill(self, pane_id: str, agent_id: str) -> None:
        if not pane_id:
            return
        proc = await asyncio.create_subprocess_exec(
            "it2",
            "close-pane",
            "--pane",
            pane_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
