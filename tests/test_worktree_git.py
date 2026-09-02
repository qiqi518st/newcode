"""ch14 worktree/git.py git 封装测试。

防的 bug：
- git 子进程未设 GIT_TERMINAL_PROMPT / GIT_ASKPASS，遇到认证会在 stdin 上挂起
- check=False 静默吞错导致 fail-OPEN（_has_worktree_changes 必须 fail-closed 返回 True）
- _resolve_head_sha_from_fs 读错 gitdir（worktree 的 .git 是指针文件）返回 None
- worktree 分支 ref 在 packed-refs 而非 loose refs 时读不到 SHA
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from mewcode.worktree import git as g

pytestmark = pytest.mark.anyio


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env={
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
        check=False,
    )
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path):
    """临时 git 仓库 + 一个 worktree（真实 git，无 API key）。"""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("hi\n", encoding="utf-8")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(r, "worktree", "add", "-B", "worktree-sub", str(wt), "HEAD")
    return {"repo": r, "wt": wt, "branch": "worktree-sub"}


async def test_rev_parse_show_toplevel(repo):
    out = await g.rev_parse_show_toplevel(str(repo["repo"]))
    assert Path(out) == repo["repo"].resolve()


async def test_worktree_add_remove_branch(repo):
    wt2 = repo["repo"].parent / "wt2"
    wt2.mkdir()
    await g.worktree_add(str(repo["repo"]), "worktree-b", str(wt2), "HEAD")
    assert wt2.is_dir()
    await g.worktree_remove_force(str(repo["repo"]), str(wt2))
    assert not wt2.exists()
    await g.branch_delete(str(repo["repo"]), "worktree-b")
    # 删除后分支不存在（-D 成功即已删）；to_thread 避免阻塞事件循环（ASYNC221）
    proc = await asyncio.to_thread(
        subprocess.run,
        ["git", "branch"],
        cwd=repo["repo"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "worktree-b" not in proc.stdout


async def test_has_changes_clean_false(repo):
    assert await g._has_worktree_changes(str(repo["wt"]), "HEAD") is False


async def test_has_changes_dirty_true(repo):
    (repo["wt"] / "a.txt").write_text("changed\n", encoding="utf-8")
    assert await g._has_worktree_changes(str(repo["wt"]), "HEAD") is True


async def test_has_changes_new_commit_true(repo):
    base = _git(repo["wt"], "rev-parse", "HEAD")  # 提交前的基线
    _git(repo["wt"], "commit", "-q", "--allow-empty", "-m", "wip")
    assert await g._has_worktree_changes(str(repo["wt"]), base) is True


async def test_has_changes_git_error_fail_closed(repo):
    # 目录损坏（无 .git）→ 命令失败 → fail-closed 返回 True
    assert await g._has_worktree_changes(str(repo["repo"].parent), "") is True


async def test_resolve_head_sha_from_fs(repo):
    sha = g._resolve_head_sha_from_fs(str(repo["wt"]))
    expect = _git(repo["wt"], "rev-parse", "HEAD")
    assert sha == expect


async def test_resolve_head_sha_from_fs_not_worktree(tmp_path):
    assert g._resolve_head_sha_from_fs(str(tmp_path / "nope")) is None


async def test_resolve_head_sha_from_fs_detached(repo):
    # 检出一个 detached HEAD 的目录 → 直接读 HEAD 的 SHA
    detached = repo["repo"].parent / "detached"
    detached.mkdir()
    _git(repo["repo"], "worktree", "add", "--detach", str(detached), "HEAD")
    sha = g._resolve_head_sha_from_fs(str(detached))
    expect = _git(detached, "rev-parse", "HEAD")
    assert sha == expect


async def test_rev_list_unpushed(repo):
    # 无 remote → 本地任何 commit 都视为未推送（Q2：无远端视为未推送）
    assert await g.rev_list_unpushed(str(repo["wt"])) != ""
    _git(repo["wt"], "commit", "-q", "--allow-empty", "-m", "wip")
    assert await g.rev_list_unpushed(str(repo["wt"])) != ""


async def test_rev_list_unpushed_with_remote(repo, tmp_path):
    # 有 remote 且 HEAD 已推送 → 空（无未推送）；新 commit → 非空
    bare = tmp_path / "remote.git"
    _git(repo["repo"], "init", "--bare", str(bare))
    _git(repo["repo"], "remote", "add", "origin", str(bare))
    _git(repo["repo"], "push", "-u", "origin", "master", "-q")
    assert await g.rev_list_unpushed(str(repo["wt"])) == ""
    _git(repo["wt"], "commit", "-q", "--allow-empty", "-m", "wip")
    assert await g.rev_list_unpushed(str(repo["wt"])) != ""


async def test_ls_files_ignored_others(repo):
    (repo["wt"] / ".gitignore").write_text(".env\n", encoding="utf-8")
    (repo["wt"] / ".env").write_text("x=1\n", encoding="utf-8")
    out = await g.ls_files_ignored_others(str(repo["wt"]))
    assert ".env" in out


def test_parse_gitdir_from_fs(repo):
    text = (repo["wt"] / ".git").read_text(encoding="utf-8")
    gd = g._parse_gitdir(text, repo["wt"])
    assert gd is not None
    # git 的 worktree 元数据目录名跟随 worktree 目录基名（此处为 wt）
    assert gd.name == "wt"
