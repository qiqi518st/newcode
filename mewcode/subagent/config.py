"""agents: 配置段（ch13 F10/F11）：三层合并（local > project > user），缺失全缺省。

与 hooks 配置同一目录族：`.mewcode/config.local.yaml` / `.mewcode/config.yaml` /
`~/.mewcode/config.yaml`，`agents:` 键局部优先（spec F11.1）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 与 hooks loader 对齐的三层目录（缺省值按 F11.1）
_AGENTS_FILE_LOCAL = ".mewcode/config.local.yaml"
_AGENTS_FILE_PROJECT = ".mewcode/config.yaml"
_AGENTS_FILE_USER = "~/.mewcode/config.yaml"


@dataclass
class AgentConfig:
    """agents: 段配置（spec F11.1），缺省全可用。"""

    enable_verifier: bool = False  # 启用内置 verifier 角色（F2.5）
    enable_subagent_background: bool = True  # 后台总闸（N7）
    async_timeout_s: float = 120.0  # 前台自动转后台阈值（F7.1）
    idle_cleanup_minutes: float = 15.0  # 空闲子 Agent 清理超时（F7.7）
    max_idle_agents: int = 10  # 空闲子 Agent 保留上限（F7.7）
    max_tasks_per_agent: int = 10  # 每子 Agent 任务总数上限（F7.7）
    max_queue_per_agent: int = 2  # 每子 Agent 排队任务上限（F7.8）
    model_tiers: dict[str, str] = field(default_factory=dict)  # 模型分层映射（F2.1/F11.1）

    def effective_enable_subagent_background(self) -> bool:
        """后台总闸生效值（字段本身缺省 True；显式方法供调用点语义清晰）。"""
        return self.enable_subagent_background


def _coerce(raw: dict[str, Any], key: str, default: Any, type_: type) -> Any:
    """数值/布尔字段容错：非法值 warning 用缺省（spec F11.1/F2.4 的配置侧等价）。"""
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, type_):
        return value
    print(
        f"subagent config: agents.{key}={value!r} 非法（应为 {type_.__name__}），"
        f"使用缺省 {default}",
        file=sys.stderr,
    )
    return default


def load_agent_config(project_root: str | Path) -> AgentConfig:
    """三层加载 agents: 段（local > project > user 追加合并，局部优先）。

    agents: 键缺失 / 文件不存在 / YAML 非法 → 全缺省或跳过该文件，不阻断（spec F11.1）。
    """
    cfg = AgentConfig()
    user_path = os.path.expanduser(_AGENTS_FILE_USER)
    project_path = str(Path(project_root) / _AGENTS_FILE_PROJECT)
    local_path = str(Path(project_root) / _AGENTS_FILE_LOCAL)

    merged: dict[str, Any] = {}
    for path in (user_path, project_path, local_path):
        if not os.path.exists(path):
            continue
        try:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            print(f"subagent config: {path} 解析失败，跳过: {exc}", file=sys.stderr)
            continue
        if isinstance(data, dict) and isinstance(data.get("agents"), dict):
            merged.update(data["agents"])

    cfg.enable_verifier = bool(_coerce(merged, "enable_verifier", False, bool))
    cfg.enable_subagent_background = bool(
        _coerce(merged, "enable_subagent_background", True, bool)
    )
    cfg.async_timeout_s = float(_coerce(merged, "async_timeout_s", 120.0, (int, float)))
    cfg.idle_cleanup_minutes = float(
        _coerce(merged, "idle_cleanup_minutes", 15.0, (int, float))
    )
    cfg.max_idle_agents = int(_coerce(merged, "max_idle_agents", 10, int))
    cfg.max_tasks_per_agent = int(_coerce(merged, "max_tasks_per_agent", 10, int))
    cfg.max_queue_per_agent = int(_coerce(merged, "max_queue_per_agent", 2, int))
    tiers = merged.get("model_tiers")
    if isinstance(tiers, dict):
        cfg.model_tiers = {str(k): str(v) for k, v in tiers.items()}
    elif "model_tiers" in merged:
        print(
            "subagent config: agents.model_tiers 非 dict，置空",
            file=sys.stderr,
        )
    return cfg
