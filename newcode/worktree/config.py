"""worktrees: 配置段（ch14 F11.1）：三层合并（local > project > user），缺省全可用。

与 agents 配置同目录族：`.newcode/config.local.yaml` / `.newcode/config.yaml` /
`~/.newcode/config.yaml`，`worktrees:` 键局部优先（镜像 subagent/config.py 模式）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 与 subagent/config.py 对齐的三层目录
_WORKTREES_FILE_LOCAL = ".newcode/config.local.yaml"
_WORKTREES_FILE_PROJECT = ".newcode/config.yaml"
_WORKTREES_FILE_USER = "~/.newcode/config.yaml"


@dataclass
class WorktreesConfig:
    """worktrees: 段配置（spec F11.1），缺省全可用。"""

    enable: bool = True  # 总开关；false 时 isolation:worktree 退化为不隔离（F11.2）
    auto_cleanup: bool = True  # 子 Agent 完成后自动清理开关（F6.1/F6.2）
    background_cleanup: bool = True  # 异常残留后台清理开关（F6.4/F6.5）
    cleanup_interval_minutes: float = 60.0  # 后台清理周期
    expire_minutes: float = 180.0  # 异常残留过期时间

    # 软链大目录清单（F4.3）
    symlink_dirs: list[str] = field(
        default_factory=lambda: ["node_modules", ".venv", "vendor"]
    )


def _coerce(raw: dict[str, Any], key: str, default: Any, type_: type) -> Any:
    """数值/布尔字段容错：非法值 warning 用缺省（F11.1 配置侧）。"""
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, type_):
        return value
    type_name = _type_name(type_)
    print(
        f"worktree config: worktrees.{key}={value!r} 非法（应为 {type_name}），"
        f"使用缺省 {default}",
        file=sys.stderr,
    )
    return default


def _type_name(type_: type) -> str:
    """类型显示名（兼容 tuple 类型：如 (int, float) → int/float）。"""
    if isinstance(type_, tuple):
        return "/".join(getattr(t, "__name__", str(t)) for t in type_)
    return getattr(type_, "__name__", str(type_))


def load_worktree_config(project_root: str | Path) -> WorktreesConfig:
    """三层加载 worktrees: 段（local > project > user 追加合并，局部优先）。

    worktrees: 键缺失 / 文件不存在 / YAML 非法 → 全缺省或跳过该文件，不阻断（F11.1）。
    """
    cfg = WorktreesConfig()
    user_path = os.path.expanduser(_WORKTREES_FILE_USER)
    project_path = str(Path(project_root) / _WORKTREES_FILE_PROJECT)
    local_path = str(Path(project_root) / _WORKTREES_FILE_LOCAL)

    merged: dict[str, Any] = {}
    for path in (user_path, project_path, local_path):
        if not os.path.exists(path):
            continue
        try:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            print(f"worktree config: {path} 解析失败，跳过: {exc}", file=sys.stderr)
            continue
        if isinstance(data, dict) and isinstance(data.get("worktrees"), dict):
            merged.update(data["worktrees"])

    cfg.enable = bool(_coerce(merged, "enable", True, bool))
    cfg.auto_cleanup = bool(_coerce(merged, "auto_cleanup", True, bool))
    cfg.background_cleanup = bool(_coerce(merged, "background_cleanup", True, bool))
    cfg.cleanup_interval_minutes = float(
        _coerce(merged, "cleanup_interval_minutes", 60.0, (int, float))
    )
    cfg.expire_minutes = float(_coerce(merged, "expire_minutes", 180.0, (int, float)))
    dirs = merged.get("symlink_dirs")
    if isinstance(dirs, list):
        cfg.symlink_dirs = [str(d) for d in dirs if str(d).strip()]
    elif "symlink_dirs" in merged:
        print(
            "worktree config: worktrees.symlink_dirs 非 list，用缺省",
            file=sys.stderr,
        )
    return cfg
