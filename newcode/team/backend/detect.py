"""后端检测（ch15 F2.4/F14）：一次性决定，启动后不运行时回退。

优先级：
1. `$TMUX` 已设 → tmux（已在 tmux 会话内）
2. `$TERM_PROGRAM == "iTerm.app"` 且 `it2` 可执行 → iterm2
3. `tmux` 二进制在 PATH → tmux（外部 spawn 新会话）
4. 否则 → in-process
"""

from __future__ import annotations

import os
import shutil

from ..types import BackendType


def detect() -> BackendType:
    """按环境一次性决定后端（F2.4）；不静默降级由调用方（显式指定时）负责。"""
    if os.environ.get("TMUX"):
        return BackendType.TMUX
    if os.environ.get("TERM_PROGRAM") == "iTerm.app" and shutil.which("it2"):
        return BackendType.ITERM2
    if shutil.which("tmux"):
        return BackendType.TMUX
    return BackendType.IN_PROCESS
