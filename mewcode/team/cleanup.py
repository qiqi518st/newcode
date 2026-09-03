"""团队清理引导（ch15 收尾 F1/F2）：提示词纪律 + execute_command 守卫。

- `TEAM_CLEANUP_DISCIPLINE`：层1——主 Agent 提示词纪律（F1.1），删除/清理团队
  必须用 TeamDelete 或 /team delete，禁止手动 git 清理团队 worktree
- `guard_team_git_cleanup(mgr, command)`：层2a——execute_command 工具守卫（F2）。
  命中「团队仍存在」的 team worktree 手动 git 清理命令 → 返回引导提示（不执行）；
  孤儿/非团队/正常 git 操作（merge/status/diff，F15 收敛需要）→ None 放行
"""

from __future__ import annotations

import re

TEAM_CLEANUP_DISCIPLINE: str = """## 团队清理纪律

删除/清理团队必须调用 TeamDelete 工具，或提示用户使用 `/team delete <name> [--force]`；
禁止手动执行 git worktree / git branch 命令清理团队成员 worktree。
"""

# git branch -D <worktree-team-<s>+<m>>
_BRANCH_DELETE_RE = re.compile(r"git\s+branch\s+-[Dd]\s+([^\s;|&]+)")
# git worktree remove [--force] <path>
_WORKTREE_REMOVE_RE = re.compile(
    r"git\s+worktree\s+remove\s+(?:--force\s+)?([^\s;|&]+)"
)
# worktree 路径段（.mewcode/worktrees/team-<s>+<m>）
_WORKTREE_PATH_RE = re.compile(r"worktrees/([^\s/;|&+]+)")


def _team_from_branch(branch: str) -> str | None:
    """`worktree-team-demo+alice` → `demo`；非 team 分支返回 None。"""
    if not branch.startswith("worktree-team-"):
        return None
    return branch[len("worktree-team-") :].split("+", 1)[0]


def _team_from_path(path: str) -> str | None:
    """`.mewcode/worktrees/team-demo+alice` → `demo`；非 team 路径返回 None。"""
    m = _WORKTREE_PATH_RE.search(path)
    if not m:
        return None
    seg = m.group(1)
    if not seg.startswith("team-"):
        return None
    return seg[len("team-") :].split("+", 1)[0]


def guard_team_git_cleanup(mgr, command: str) -> str | None:
    """层2a 守卫（F2.1-F2.4）。

    - 命中：`git branch -D` / `git worktree remove` 且目标为 team worktree 且团队配置仍存在
      → 返回「请改用 /team delete」引导提示（不执行、不弹权限确认）
    - 放行（返回 None）：孤儿（团队已删）、非团队 worktree、以及一切非清理类 git
      操作（merge/status/diff——F15 收敛合并依赖）
    """
    candidates: list[str] = []
    for m in _BRANCH_DELETE_RE.finditer(command):
        t = _team_from_branch(m.group(1))
        if t:
            candidates.append(t)
    for m in _WORKTREE_REMOVE_RE.finditer(command):
        t = _team_from_path(m.group(1))
        if t:
            candidates.append(t)
    if not candidates:
        return None
    for t in candidates:
        if mgr.get(t) is not None:
            return (
                f"请改用 /team delete {t} --force 或 TeamDelete 工具清理团队"
                "（自动按正确顺序：kill → worktree remove → branch -D → 删配置）。"
                "禁止手动 git 清理团队成员 worktree。"
            )
    return None
