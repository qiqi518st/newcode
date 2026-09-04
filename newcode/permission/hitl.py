"""人在回路（HITL）数据结构（L5）"""

from dataclasses import dataclass
from typing import Literal


@dataclass
class HITLRequest:
    """待批准的工具调用信息"""

    tool_name: str  # 友好名
    params_preview: str  # 关键参数预览
    reason: str  # 触发原因（来自 CheckResult.reason）


@dataclass
class HITLResponse:
    """用户在确认框中的选择"""

    action: Literal["allow_once", "allow_always", "deny"]
