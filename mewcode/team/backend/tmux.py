"""tmux 后端（ch15 F3/F15/F16）：独立窗格跑完整 `mewcode --team-member` 实例。

- spawn：`tmux split-window -h -P -F "#{pane_id}" -- <cmd>` 捕获 pane_id（F15）
  - 会话外（`$TMUX` 未设但 detect 得 tmux）：`tmux new-session -d` detached（F16）；
    失败抛 BackendUnavailableError，**不回落 in-process**（F2.5）
- 命令构造：`python -m mewcode --team-member ...`，**必带预生成的 `--agent-id`**
  （F3.2：子进程无需读 Lead 未写完的 config.json 找自己）；参数经 shlex.quote 转义
- `initial_prompt` 不走命令行——由 spawn_teammate 在 spawn 前预写 mailbox（F2.6）
- wake：`tmux send-keys -t <pane_id> "" Enter` 触发子进程 stdin reader（F3.3）
- kill：`tmux kill-pane -t <pane_id>`（忽略不存在，F3.4）
"""

from __future__ import annotations

import asyncio
import os
import sys

from ..types import BackendType, BackendUnavailableError
from . import SpawnRequest


def build_member_cmd(req: SpawnRequest) -> list[str]:
    """构造 `mewcode --team-member` 命令行（含预生成 --agent-id，F3.2/F15）。"""
    parts = [
        sys.executable,
        "-m",
        "mewcode",
        "--team-member",
        "--team",
        req.team_name,
        "--member",
        req.member_name,
        "--agent-id",
        req.agent_id,
        "--session-dir",
        req.session_dir,
        "--worktree",
        req.worktree_path,
    ]
    if req.agent_type:
        parts += ["--agent-type", req.agent_type]
    if req.model:
        parts += ["--model", req.model]
    if req.plan_mode_required:
        parts += ["--plan-mode"]
    return parts


class TmuxBackend:
    """tmux 后端（F15/F16）。"""

    def __init__(self, **_deps) -> None:
        self._deps = _deps

    def type(self) -> BackendType:
        return BackendType.TMUX

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        """split-window 起子进程；返回 (pane_id, agent_id)。"""
        cmd = build_member_cmd(req)
        args = ["tmux", "split-window", "-h", "-P", "-F", "#{pane_id}", "--", *cmd]
        if not os.environ.get("TMUX"):
            # 会话外：detached 新会话（F16）；失败抛错不回落 in-process（F2.5）
            session = f"mewcode-team-{req.team_name}-{req.member_name}"
            args = ["tmux", "new-session", "-d", "-s", session, *cmd]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""},
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            raise BackendUnavailableError(
                f"tmux spawn 超时（{cmd[0]} ...）：检查 tmux 会话"
            )
        if proc.returncode != 0:
            raise BackendUnavailableError(
                f"tmux spawn 失败（rc={proc.returncode}）: "
                f"{stderr.decode('utf-8', 'replace').strip() or '无 stderr'}"
            )
        pane_id = stdout.decode("utf-8", "replace").strip()
        if not pane_id:
            raise BackendUnavailableError("tmux spawn 未返回 pane_id")
        return pane_id, req.agent_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        """send-keys 回车触发子进程 stdin reader → 立即轮询 mailbox（F3.3）。"""
        if not pane_id:
            return
        await asyncio.create_subprocess_exec(
            "tmux",
            "send-keys",
            "-t",
            pane_id,
            "",
            "Enter",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def kill(self, pane_id: str, agent_id: str) -> None:
        """kill-pane；pane 不存在错误忽略（F3.4）。"""
        if not pane_id:
            return
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "kill-pane",
            "-t",
            pane_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
