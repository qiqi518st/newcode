"""Coordinator Mode（ch15 F14/F52-F55）：独立于 Team 的可选能力。

- `is_enabled(cfg)`：双锁——config feature flag（features.coordinator_mode）+ 环境变量
  MEWCODE_COORDINATOR_MODE（1/true/yes，大小写不敏感），两把都开才生效（F14.1/F52）
- `allowed_tools()`：COORDINATOR_ALLOWED_TOOLS 常量（F53）——调度 + 读类 + Bash
- `system_prompt_suffix()`：四阶段工作流 + 派完停手纪律（F55）——纯 prompt 引导不强制
- 启动时一次判定，会话内固定；运行时不可解锁（N8，唯一解除=重启）
"""

from __future__ import annotations

import os

from ..config.features import FeaturesConfig

# 收窄后 Lead 保留的工具（内部名；F53）
COORDINATOR_ALLOWED_TOOLS: list[str] = [
    "Agent",
    "TeamCreate",
    "TeamDelete",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "SendMessage",
    "read_file",
    "glob",
    "grep",
    "bash",
]

_TRUTHY = frozenset({"1", "true", "yes"})


def env_truthy(v: str) -> bool:
    """环境变量 truthy 判定（1/true/yes，大小写不敏感，F52）。"""
    return v.strip().lower() in _TRUTHY


def is_enabled(cfg: FeaturesConfig) -> bool:
    """双锁全开才生效（F14.1/F52）：feature flag AND 环境变量。"""
    if not cfg.enable or not cfg.coordinator_mode:
        return False
    return env_truthy(os.environ.get("MEWCODE_COORDINATOR_MODE", ""))


def allowed_tools() -> list[str]:
    """Coordinator 收窄工具白名单（F53）。"""
    return list(COORDINATOR_ALLOWED_TOOLS)


SYSTEM_PROMPT_SUFFIX = """## Coordinator Mode

你是团队的协调者（Coordinator），只调度、不亲手改代码。

四阶段工作流：
1. **Research** — 派队员并行调查代码库、定位文件、理解问题。
2. **Synthesis** — 你亲自阅读队员的调查结果，理解问题，撰写实施规格。
   不得把理解能力委托出去。
3. **Implementation** — 派队员按规格修改代码并提交。
4. **Verification** — 派队员测试改动是否正确。

纪律（派完就停手等汇报）：
- 派出 Agent / SendMessage 之后，**禁止**立刻用 read_file/glob/grep/bash 自己探索；
  也**禁止**用 sleep 或轮询 TaskList 凑时间。任务完成时系统会推送
  `<task-notification>`，你下一轮被唤醒后再继续。
- 派完队员后唯一该做的事：发一行总结「已派 N 名队员探索 X，等结果」，让本轮结束。
- 允许你自己用读类工具的场景仅限：Research 第一次目标定位；Synthesis 阶段读队员
  产出的报告文件；Verification 阶段 git diff / git status 等收敛操作。
"""


def system_prompt_suffix() -> str:
    """四阶段 + 派完停手纪律提示词（F55）。"""
    return SYSTEM_PROMPT_SUFFIX
