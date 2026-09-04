"""Manager 聚合（ch14 F2.3/F2.4/F6.5）：构造校验 / 扫描 / 访问器 / 周期清理。

- __init__：同步 rev-parse 校验仓库根（失败抛 WorktreeError，main.py 降级 None）→
  mkdir worktree_dir → load_session（失效清空）→ 扫描 active（纯 fs 读）→
  check_gitignore（F1.4 只警告不改）
- list / get / current_session：查询访问器
- run：周期 sweep_stale（F6.5，首轮立即清理）
- create / enter / exit / remove / auto_cleanup / sweep_stale 由 create.py /
  lifecycle.py / sweep.py 以方法绑定方式挂载（plan 文件拆分）
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import WorktreesConfig
from .git import _resolve_head_sha_from_fs
from .session import WorktreeSession, load_session, save_session
from .types import Worktree, WorktreeError

_GITIGNORE_LINES = (".newcode/worktrees/", ".newcode/worktree_session.json")


class Manager:
    """Worktree 管理器（构造即校验仓库根；main.py 捕获异常降级为 None，N11）。"""

    def __init__(self, repo_root: str, cfg: WorktreesConfig) -> None:
        self.repo_root = str(Path(repo_root).resolve())
        self.cfg = cfg
        self.worktree_dir = Path(self.repo_root) / ".newcode" / "worktrees"
        self.session_file = Path(self.repo_root) / ".newcode" / "worktree_session.json"
        self.symlink_dirs = list(cfg.symlink_dirs)
        self._lock = asyncio.Lock()
        self.active: dict[str, Worktree] = {}
        self._current_session: WorktreeSession | None = None

        self._verify_repo_root()
        self.worktree_dir.mkdir(parents=True, exist_ok=True)
        self._load_session()
        self._scan_active()
        self.check_gitignore()

    # ── 构造期 ─────────────────────────────────────────────
    def _verify_repo_root(self) -> None:
        """校验 repo_root 是 git 仓库根；失败抛 WorktreeError（F2.4/N11）。"""
        try:
            top = subprocess.run(
                ["git", "-C", self.repo_root, "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,  # 手动检查 returncode（非 git 仓库）
            )
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            raise WorktreeError(
                f"不是 git 仓库或 git 不可用: {self.repo_root}"
            ) from exc
        if top.returncode != 0:
            raise WorktreeError(f"不是 git 仓库根: {self.repo_root}")
        top_path = Path(top.stdout.strip())
        if top_path.resolve() != Path(self.repo_root).resolve():
            raise WorktreeError(f"{self.repo_root} 不是仓库根（实际根 {top_path}）")

    def _load_session(self) -> None:
        """载入 session；指向的 worktree 不存在 → 清空 + 警告（F10.2）。"""
        s = load_session(self.session_file)
        if s is None:
            return
        if not Path(s.worktree_path).exists():
            print(
                "worktree: session 指向的 worktree 已不存在，已清空",
                file=sys.stderr,
            )
            save_session(self.session_file, None)
            return
        self._current_session = s

    def _scan_active(self) -> None:
        """扫描 worktree_dir 子目录还原 active（纯 fs 读，F2.4）。"""
        if not self.worktree_dir.is_dir():
            return
        for child in self.worktree_dir.iterdir():
            if not child.is_dir():
                continue
            name = child.name
            sha = _resolve_head_sha_from_fs(str(child))
            if sha is None:
                continue
            # 扫描重建无法得知来源 → 保守 manual=True（不误入自动清理）
            self.active[name] = Worktree(
                name=name,
                path=str(child),
                branch=f"worktree-{name}",
                based_on="",
                head_commit=sha,
                created=datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc),
                manual=True,
            )

    def check_gitignore(self) -> None:
        """F1.4：根 .gitignore 缺两行 → stderr 警告，不修改（尊重用户配置）。"""
        gi = Path(self.repo_root) / ".gitignore"
        text = gi.read_text(encoding="utf-8", errors="replace") if gi.is_file() else ""
        missing = [ln for ln in _GITIGNORE_LINES if ln not in text]
        if missing:
            print(
                "worktree: 项目 .gitignore 未包含 "
                + "、".join(missing)
                + "（worktree 会出现在 git status；按 F1.4 只警告不修改）",
                file=sys.stderr,
            )

    # ── 访问器 ─────────────────────────────────────────────
    def list(self) -> list[Worktree]:
        return sorted(self.active.values(), key=lambda w: w.name)

    def get(self, name: str) -> Worktree | None:
        return self.active.get(name)

    def current_session(self) -> WorktreeSession | None:
        return self._current_session

    # ── 周期后台清理（F6.5）────────────────────────────────
    async def run(self) -> None:
        """周期 sweep_stale：首轮立即清理，然后按 cleanup_interval 循环。"""
        from datetime import timedelta

        while self.cfg.background_cleanup:
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(
                    minutes=self.cfg.expire_minutes
                )
                await self.sweep_stale(cutoff)
            except Exception:  # noqa: S110, BLE001 - 后台循环不因单轮失败而退出
                pass
            await asyncio.sleep(self.cfg.cleanup_interval_minutes * 60)


# ── 方法绑定：create/lifecycle/sweep 按 plan 文件拆分 ──────
from .create import create
from .lifecycle import auto_cleanup, enter, exit, remove
from .sweep import sweep_stale

Manager.create = create  # type: ignore[attr-defined]
Manager.enter = enter  # type: ignore[attr-defined]
Manager.exit = exit  # type: ignore[attr-defined]
Manager.remove = remove  # type: ignore[attr-defined]
Manager.auto_cleanup = auto_cleanup  # type: ignore[attr-defined]
Manager.sweep_stale = sweep_stale  # type: ignore[attr-defined]
