"""slug 安全校验与命名（ch14 F1.1/F1.2/F1.3）：防 LLM 输入触发路径遍历。

- validate_slug：字符集 [a-zA-Z0-9._-] 每段 + 总长 ≤64 + 拒绝 . / .. 段 + 拒绝 // 与首末 /
- flat_slug / branch_name：嵌套 slug 的 `/`→`+` 避免 Git 分支 D/F 冲突
- is_auto_name：自动创建（子 Agent）临时 worktree 名的识别（sweep 分类用，兼容 wf-）
- random_agent_name：自动创建 worktree 名唯一入口（agent-a<7hex>）
"""

from __future__ import annotations

import re
import secrets

_SLUG_SEG_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
_MAX_SLUG_LEN = 64
_AUTO_NAME_RE = re.compile(r"^agent-a[0-9a-f]{7}$")


def validate_slug(name: str) -> None:
    """校验 slug；非法抛 ValueError 带具体原因（F1.1）。"""
    if not name:
        raise ValueError("slug 不能为空")
    if len(name) > _MAX_SLUG_LEN:
        raise ValueError(f"slug 长度超过上限 {_MAX_SLUG_LEN}")
    if name.startswith("/") or name.endswith("/"):
        raise ValueError("slug 不能以 / 开头或结尾")
    if "//" in name:
        raise ValueError("slug 不允许连续 //")
    for seg in name.split("/"):
        if seg in (".", ".."):
            raise ValueError(f"slug 段不允许为 {seg!r}")
        if not _SLUG_SEG_RE.match(seg):
            raise ValueError(f"slug 段 {seg!r} 含非法字符（仅限字母数字 . _ -）")


def flat_slug(name: str) -> str:
    """嵌套 slug 的 `/`→`+`（目录名与分支名共用，F1.2）。"""
    return name.replace("/", "+")


def branch_name(name: str) -> str:
    """分支名：worktree-<flat_slug>（F1.2）。"""
    return f"worktree-{flat_slug(name)}"


def is_auto_name(name: str) -> bool:
    """是否为自动创建（子 Agent）的临时 worktree 名（F1.3/F6.4，兼容 wf- 前缀）。

    防的 bug：手动创建者误用 agent-/wf- 前缀会被后台清理误删。
    """
    return bool(_AUTO_NAME_RE.match(name)) or name.startswith("wf-")


def random_agent_name() -> str:
    """生成自动创建 worktree 的临时名：agent-a<7位hex>（F8.2）。"""
    return f"agent-a{secrets.token_hex(4)[:7]}"
