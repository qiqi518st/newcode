"""权限系统基础类型：Decision、CheckResult、TargetInfo"""

from dataclasses import dataclass
from enum import Enum


class Decision(Enum):
    """权限判定中间值"""

    ALLOW = "allow"  # 放行
    DENY = "deny"  # 拒绝（含原因）
    ASK = "ask"  # 需要用户确认（进入第五层）


@dataclass
class CheckResult:
    """权限检查结果"""

    decision: Decision
    reason: str = ""  # 原因文案


@dataclass
class TargetInfo:
    """参数提取结果"""

    target: str  # 提取的匹配目标（命令串 / 路径 / ""）
    is_file: bool  # 是否文件类操作
    ok: bool  # 解析是否成功
