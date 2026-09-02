"""worktree 隔离子 Agent 执行（ch14 F8.2/F8.3/F8.4）：_execute_with_worktree。

- 子 Agent 在 worktree **创建后**构造（launcher + role 注入），可注入隔离沙箱与写权限：
  - sandbox_root = worktree 路径（权限沙箱根跟随工作目录，F4.4；绝对路径出 worktree 即拒）
  - permission_mode = acceptEdits（worktree 内写自动放行；命令仍 ASK→DENY）
- 流程：auto_name → wm.create(manual=False) → make_sub_agent(隔离参数) →
  build_worktree_notice + prompt → with_cwd(wt.path) 内 run_to_completion
  （MaxTurnsReached 捕获）→ auto_cleanup → kept 时追加保留通知给主 Agent review（F6.2）
- isolation 强制前台（F8.4 本期最小实现，忽略 run_in_background）
- worktree 包不依赖 agent → 无导入循环
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..permission.modes import PermissionMode
from ..subagent.errors import MaxTurnsReached
from ..subagent.launcher import LaunchResult
from ..worktree import Manager, random_agent_name
from ..worktree.notice import build_worktree_notice
from .cwd import with_cwd


async def _execute_with_worktree(
    manager: Manager,
    launcher,
    role,
    prompt: str,
    *,
    model_override: str = "",
) -> LaunchResult:
    """在临时 worktree 中执行子 Agent，完成后自动清理（F8.2）。

    子 Agent 权限沙箱 = worktree 路径 + acceptEdits（worktree 内写自动放行）。
    返回 LaunchResult(status="completed", text=...)；失败带 error。
    """
    name = random_agent_name()
    wt = await manager.create(name, "HEAD", manual=False)
    # 隔离构造：沙箱根=worktree + 模式 acceptEdits（worktree 内写自动放行；命令仍走权限）
    sub, _ = launcher.make_sub_agent(
        role,
        is_background=False,
        model_override=model_override,
        permission_mode=PermissionMode.ACCEPT_EDITS,
        sandbox_root=wt.path,
    )
    parent_cwd = str(Path.cwd())
    task = build_worktree_notice(parent_cwd, wt.path) + "\n\n" + prompt

    text = ""
    err = ""
    try:
        with with_cwd(wt.path):
            try:
                text = await sub.run_to_completion(task)
            except MaxTurnsReached as exc:
                text = exc.final_text or ""
                err = "子 Agent 达到最大轮数"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 终止原因转结果
                err = str(exc)
    finally:
        try:
            report = await manager.auto_cleanup(name)
            if report.kept:
                text += f"\n[Worktree 保留在 {report.path},分支 {report.branch}]"
        except Exception as exc:  # noqa: BLE001 - 清理失败不掩盖子 Agent 结果
            text += f"\n[Worktree 清理失败: {exc}]"

    if err:
        return LaunchResult(error=err, text=text)
    return LaunchResult(status="completed", text=text)
