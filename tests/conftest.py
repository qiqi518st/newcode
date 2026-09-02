"""pytest 配置：anyio 自动处理 async 测试 + 临时 git 仓库 fixture（ch14）。"""

import subprocess

import pytest

_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def _git(repo, *args: str) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
        check=False,
    )
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def git_repo(tmp_path):
    """临时 git 仓库（真实 git、无 API key、隔离 GIT_CONFIG），供 worktree 测试。"""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("hi\n", encoding="utf-8")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    return r
