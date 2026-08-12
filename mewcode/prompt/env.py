"""环境信息采集与格式化（spec F3，N12 快速有界、可降级）"""

import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime

_GIT_TIMEOUT = 1.0  # git 子进程超时（秒），保证快速有界


@dataclass
class EnvContext:
    """运行环境信息；git 字段取不到时为 None（N12 降级）"""

    cwd: str
    platform: str
    datetime: str
    timezone: str
    version: str
    provider: str
    model: str
    git_branch: str | None = None
    git_dirty: bool | None = None


def collect_env(cwd: str, version: str, provider: str, model: str) -> EnvContext:
    """采集环境信息；git 状态带超时，失败/超时降级为 None，不抛异常"""
    now = datetime.now().astimezone()
    env = EnvContext(
        cwd=cwd,
        platform=platform.platform(),
        datetime=now.strftime("%Y-%m-%d %H:%M:%S"),
        timezone=str(now.tzinfo) if now.tzinfo else "",
        version=version,
        provider=provider,
        model=model,
    )
    branch, dirty = None, None
    try:
        branch, dirty = _collect_git(cwd)
    except Exception:  # noqa: BLE001, S110 — N12 降级，不中断会话
        pass
    env.git_branch = branch
    env.git_dirty = dirty
    return env


def _collect_git(cwd: str) -> tuple[str | None, bool | None]:
    """git 状态采集：分支 + 是否有未提交修改；非 git 仓库/失败/超时降级"""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "-b"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    if proc.returncode != 0:
        return None, None
    lines = proc.stdout.splitlines()
    branch: str | None = None
    if lines and lines[0].startswith("## "):
        branch = lines[0][3:].split("...")[0].strip()
    dirty = len(lines) > 1
    return branch, dirty


def format_env(env: EnvContext) -> str:
    """把环境信息拼成一段供模型感知的文本；缺失项省略该行"""
    rows = [
        f"工作目录: {env.cwd}",
        f"平台: {env.platform}",
        f"当前时间: {env.datetime} {env.timezone}".rstrip(),
        f"应用版本: {env.version}",
        f"当前模型: {env.provider}/{env.model}",
    ]
    if env.git_branch is not None:
        state = "有未提交修改" if env.git_dirty else "无未提交修改"
        rows.append(f"git: 分支 {env.git_branch}，{state}")
    return "\n".join(rows)
