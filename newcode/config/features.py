"""features: 配置段（ch15 TD-1）：三层合并（local > project > user），缺省全关。

- `features.coordinator_mode`：COORDINATOR_MODE feature flag（F14.1 第一把锁）
- `features.fork_teammate`：FORK_TEAMMATE（F11/G11，默认关）
与 hooks/agents/worktrees 同目录族：`.newcode/config.local.yaml` /
`.newcode/config.yaml` / `~/.newcode/config.yaml`，`features:` 键局部优先。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_FEATURES_FILE_LOCAL = ".newcode/config.local.yaml"
_FEATURES_FILE_PROJECT = ".newcode/config.yaml"
_FEATURES_FILE_USER = "~/.newcode/config.yaml"


@dataclass
class FeaturesConfig:
    """features: 段配置（TD-1），缺省全关（能力默认不开放）。"""

    enable: bool = True  # 团队/协作能力总开关
    coordinator_mode: bool = False  # COORDINATOR_MODE feature flag（F14.1）
    fork_teammate: bool = False  # FORK_TEAMMATE（F11/G11）


def _coerce(raw: dict[str, Any], key: str, default: bool) -> bool:
    """布尔字段容错：非法值 warning 用缺省。"""
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, bool):
        return value
    print(
        f"features config: features.{key}={value!r} 非法（应为 bool），"
        f"使用缺省 {default}",
        file=sys.stderr,
    )
    return default


def load_features_config(project_root: str | Path) -> FeaturesConfig:
    """三层加载 features: 段（local > project > user 追加合并，局部优先）。

    features: 键缺失 / 文件不存在 / YAML 非法 → 全缺省或跳过该文件，不阻断。
    """
    cfg = FeaturesConfig()
    user_path = os.path.expanduser(_FEATURES_FILE_USER)
    project_path = str(Path(project_root) / _FEATURES_FILE_PROJECT)
    local_path = str(Path(project_root) / _FEATURES_FILE_LOCAL)

    merged: dict[str, Any] = {}
    for path in (user_path, project_path, local_path):
        if not os.path.exists(path):
            continue
        try:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            print(f"features config: {path} 解析失败，跳过: {exc}", file=sys.stderr)
            continue
        if isinstance(data, dict) and isinstance(data.get("features"), dict):
            merged.update(data["features"])

    cfg.enable = _coerce(merged, "enable", True)
    cfg.coordinator_mode = _coerce(merged, "coordinator_mode", False)
    cfg.fork_teammate = _coerce(merged, "fork_teammate", False)
    return cfg
