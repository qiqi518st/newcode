"""create + 快速恢复 + 创建后设置（ch14 F3/F4）。

- create：validate_slug → 锁内查重 → 构建路径/分支 → 快速恢复（目录已存在零 git）→
  git worktree add -B → 创建后设置（best-effort）→ 读 head_sha → 入 active
- _perform_post_creation_setup：A 复制本地配置 / B git hooks / C 软链大目录 /
  D .worktreeinclude 补被忽略文件（N2：任何子步骤失败仅警告不中断）
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .git import (
    _resolve_head_sha_from_fs,
    _run_git,
    config_get,
    config_set_hooks_path,
    ls_files_ignored_others,
    worktree_add,
)
from .slug import branch_name, flat_slug, is_auto_name, validate_slug
from .types import Worktree, WorktreeExistsError, WorktreeGitError

if TYPE_CHECKING:
    from .manager import Manager

# F4.1 A：复制的本地配置项（跳过 worktrees/ sessions/ memory/ monitor/ 运行时数据）
_COPY_ITEMS = [
    "config.local.yaml",
    "config.yaml",
    "permissions.local.yaml",
    "permissions.yaml",
    "agents",
    "skills",
]


async def create(
    self: Manager, name: str, base_ref: str = "HEAD", manual: bool = False
) -> Worktree:
    """创建 worktree（F3.1）；目录已存在走快速恢复（G5，零 git 子进程）。"""
    validate_slug(name)
    # F1.3：手动创建不得使用自动命名前缀（避免误入异常清理）
    if manual and is_auto_name(name):
        raise ValueError(f"手动创建不得使用自动命名前缀（agent-/wf-）: {name}")
    flat = flat_slug(name)
    wt_path = self.worktree_dir / flat
    branch = branch_name(name)

    async with self._lock:
        if name in self.active:
            raise WorktreeExistsError(f"worktree 已存在: {name}")

    # 快速恢复：目录已存在 → 纯 fs 读还原，不调 git（F3.1.4/G5）
    if wt_path.exists():
        sha = _resolve_head_sha_from_fs(str(wt_path))
        if sha is None:
            raise WorktreeGitError(
                ["worktree", "add"], "目录已存在但无法从文件系统还原 HEAD"
            )
        wt = Worktree(
            name=name,
            path=str(wt_path),
            branch=branch,
            based_on=base_ref,
            head_commit=sha,
            created=datetime.now(timezone.utc),
            manual=manual,
        )
        async with self._lock:
            self.active[name] = wt
        return wt

    try:
        await worktree_add(self.repo_root, branch, str(wt_path), base_ref)
    except WorktreeGitError:
        # 失败清理残留目录后重抛（F3.1.5）
        shutil.rmtree(wt_path, ignore_errors=True)
        raise

    # 创建后设置 best-effort（N2：失败仅警告，不阻塞创建）
    await _perform_post_creation_setup(self.repo_root, wt_path, self.symlink_dirs)

    head_sha = await _run_git(str(wt_path), "rev-parse", "HEAD")
    wt = Worktree(
        name=name,
        path=str(wt_path),
        branch=branch,
        based_on=base_ref,
        head_commit=head_sha,
        created=datetime.now(timezone.utc),
        manual=manual,
    )
    async with self._lock:
        self.active[name] = wt
    return wt


async def _perform_post_creation_setup(
    repo_root: str, wt_path: Path, symlink_dirs: list[str]
) -> None:
    """四类初始化（F4）；每个子步骤 try/except 仅 stderr 警告。"""
    steps: list[tuple[str, object, tuple]] = [
        ("复制本地配置", _setup_copy_config, (repo_root, wt_path)),
        ("配置 git hooks", _setup_hooks, (repo_root, wt_path)),
        ("软链大目录", _setup_symlink_dirs, (repo_root, wt_path, symlink_dirs)),
        (".worktreeinclude", _setup_worktreeinclude, (repo_root, wt_path)),
    ]
    for label, fn, args in steps:
        try:
            await fn(*args)  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001 - best-effort（N2）
            print(f"worktree: 创建后设置 {label} 失败（忽略）: {exc}", file=sys.stderr)


async def _setup_copy_config(repo_root: str, wt_path: Path) -> None:
    """A 复制本地配置（F4.1）：目标已存在跳过、源缺失跳过。"""
    src = Path(repo_root) / ".mewcode"
    if not src.is_dir():
        return
    dst = wt_path / ".mewcode"
    dst.mkdir(parents=True, exist_ok=True)
    for item in _COPY_ITEMS:
        s = src / item
        if not s.exists():
            continue
        d = dst / item
        if d.exists():
            continue
        if s.is_dir():
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)


async def _setup_hooks(repo_root: str, wt_path: Path) -> None:
    """B 配置 git hooks（F4.2）：优先 .husky/，回退主仓库 core.hooksPath。"""
    hooks_path: str | None = None
    if (Path(repo_root) / ".husky").is_dir():
        hooks_path = str(Path(repo_root) / ".husky")
    else:
        cfg = await config_get(repo_root, "core.hooksPath")
        if cfg:
            hooks_path = cfg
    if hooks_path:
        await config_set_hooks_path(str(wt_path), hooks_path)


async def _setup_symlink_dirs(
    repo_root: str, wt_path: Path, symlink_dirs: list[str]
) -> None:
    """C 软链大目录（F4.3）：主存在且 wt 缺 → os.symlink（Windows 失败跳过）。"""
    for d in symlink_dirs:
        src = Path(repo_root) / d
        dst = wt_path / d
        if src.exists() and not dst.exists():
            try:
                os.symlink(src, dst)
            except OSError as exc:
                print(f"worktree: 软链 {d} 失败: {exc}", file=sys.stderr)


async def _setup_worktreeinclude(repo_root: str, wt_path: Path) -> None:
    """D .worktreeinclude（F4.4）：按 glob 模式复制被忽略但运行需要的文件。"""
    inc = Path(repo_root) / ".worktreeinclude"
    if not inc.is_file():
        return
    patterns = [
        ln.strip()
        for ln in inc.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not patterns:
        return
    out = await ls_files_ignored_others(repo_root)
    for line in out.splitlines():
        rel = line.strip()
        if not rel:
            continue
        if not any(
            fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(Path(rel).name, pat)
            for pat in patterns
        ):
            continue
        src = Path(repo_root) / rel
        dst = wt_path / rel
        if dst.exists():
            continue
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif src.is_dir():
            shutil.copytree(src, dst)
