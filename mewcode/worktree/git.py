"""git 子进程封装（ch14 唯一 git 出入口）+ 快速恢复 fs 读。

- `_run_git`：asyncio.create_subprocess_exec 非阻塞；统一 GIT_TERMINAL_PROMPT=0 /
  GIT_ASKPASS="" / stdin=DEVNULL / timeout；check=True 失败抛 WorktreeGitError（N10）
- 高层封装：rev_parse / worktree add|remove / branch -D / status / rev-list / ls-files / config
- `_has_worktree_changes`：fail-closed（git 出错宁可保留，F5.4）
- `_resolve_head_sha_from_fs`：快速恢复，纯 fs 读还原 HEAD SHA，零 git 子进程（G5）
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .types import WorktreeGitError

_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""}
_GIT_TIMEOUT = 30.0


async def _run_git(
    work_dir: str, *args: str, check: bool = True, timeout: float = _GIT_TIMEOUT
) -> str:
    """运行 git，返回 stdout（rstrip 换行）。check=True 失败抛 WorktreeGitError。"""
    env = dict(os.environ)
    env.update(_GIT_ENV)
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=work_dir,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        if check:
            raise WorktreeGitError(list(args), "git 未安装或不在 PATH") from exc
        return ""
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        if check:
            raise WorktreeGitError(list(args), "git 命令超时")
        return ""
    stdout = out.decode("utf-8", errors="replace").rstrip("\n")
    stderr = err.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        if check:
            raise WorktreeGitError(list(args), stderr)
        return ""
    return stdout


# ── 高层封装 ─────────────────────────────────────────────
async def rev_parse_show_toplevel(work_dir: str) -> str:
    return await _run_git(work_dir, "rev-parse", "--show-toplevel")


async def worktree_add(work_dir: str, branch: str, path: str, base: str) -> str:
    return await _run_git(work_dir, "worktree", "add", "-B", branch, path, base)


async def worktree_remove_force(work_dir: str, path: str) -> str:
    return await _run_git(work_dir, "worktree", "remove", "--force", path)


async def branch_delete(work_dir: str, branch: str) -> str:
    return await _run_git(work_dir, "branch", "-D", branch)


async def status_porcelain(work_dir: str, *, check: bool = True) -> str:
    return await _run_git(work_dir, "status", "--porcelain", check=check)


async def rev_list_count(work_dir: str, base: str, *, check: bool = True) -> int:
    out = await _run_git(work_dir, "rev-list", "--count", f"{base}..HEAD", check=check)
    try:
        return int(out.strip() or "0")
    except ValueError:
        return 0


async def rev_list_unpushed(work_dir: str, *, check: bool = True) -> str:
    """未推送 commit 检测：HEAD --not --remotes 非空即有（本地 refs，无网络）。"""
    return await _run_git(
        work_dir,
        "rev-list",
        "--max-count=1",
        "HEAD",
        "--not",
        "--remotes",
        check=check,
    )


async def ls_files_ignored_others(work_dir: str, *, check: bool = True) -> str:
    """列出被忽略文件（F4.4 .worktreeinclude 数据源）。"""
    return await _run_git(
        work_dir,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--directory",
        check=check,
    )


async def config_get(work_dir: str, key: str, *, check: bool = False) -> str:
    return await _run_git(work_dir, "config", "--get", key, check=check)


async def config_set_hooks_path(work_dir: str, path: str) -> str:
    return await _run_git(work_dir, "config", "core.hooksPath", path)


async def current_branch(work_dir: str) -> str:
    return await _run_git(work_dir, "rev-parse", "--abbrev-ref", "HEAD")


async def current_head(work_dir: str) -> str:
    return await _run_git(work_dir, "rev-parse", "HEAD")


# ── 变更检测（F5.4）──────────────────────────────────────
async def _has_worktree_changes(wt_path: str, base_commit: str) -> bool:
    """有未提交修改或新增 commit → True；git 出错 fail-closed 返回 True（宁可保留）。"""
    try:
        if await status_porcelain(wt_path):
            return True
        if base_commit:
            return await rev_list_count(wt_path, base_commit) > 0
    except WorktreeGitError:
        return True
    return False


# ── 快速恢复：纯 fs 读（G5，零 git 子进程）────────────────
def _resolve_head_sha_from_fs(wt_path: str) -> str | None:
    """读 .git 指针 → gitdir → HEAD → ref（含 packed-refs 兜底）还原 SHA；失败返回 None。"""
    try:
        p = Path(wt_path)
        gitfile = p / ".git"
        if gitfile.is_file():
            gitdir = _parse_gitdir(gitfile.read_text(encoding="utf-8"), p)
        elif gitfile.is_dir():
            gitdir = gitfile
        else:
            return None
        if gitdir is None:
            return None
        head_text = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if head_text.startswith("ref: "):
            ref = head_text[len("ref: ") :].strip()
            common = _resolve_commondir(gitdir)
            sha = _read_ref(common, ref)
        else:
            sha = head_text  # detached HEAD
        return sha or None
    except OSError:
        return None


def _parse_gitdir(text: str, base: Path) -> Path | None:
    for line in text.splitlines():
        if line.startswith("gitdir:"):
            d = line.split(":", 1)[1].strip()
            dp = Path(d)
            return dp if dp.is_absolute() else base / dp
    return None


def _resolve_commondir(gitdir: Path) -> Path:
    cd_file = gitdir / "commondir"
    if cd_file.is_file():
        d = cd_file.read_text(encoding="utf-8").strip()
        dp = Path(d)
        return dp if dp.is_absolute() else gitdir / dp
    return gitdir


def _read_ref(common: Path, ref: str) -> str | None:
    loose = common / ref
    if loose.is_file():
        return loose.read_text(encoding="utf-8").strip() or None
    packed = common / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line.startswith(("#", "^")):
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == ref:
                return parts[0]
    return None
