"""MewCode 权限系统：五层防御

对外暴露：
- Decision、CheckResult、TargetInfo — 基础类型
- PermissionMode、ToolCategory — 枚举
- HITLRequest、HITLResponse — 人在回路
- PermissionChecker — 权限检查器入口
- RuleLayers、RuleSet、Rule — 规则分层
"""

from .checker import (
    PermissionChecker,
    categorize,
    extract_target,
    friendly_name,
    internal_name,
)
from .engine import RuleEngine
from .hitl import HITLRequest, HITLResponse
from .modes import MODE_MATRIX, PermissionMode, ToolCategory, resolve_mode
from .rules import Rule, RuleLayers, RuleSet, load_rules, match_pattern
from .types import CheckResult, Decision, TargetInfo

__all__ = [
    "MODE_MATRIX",
    "CheckResult",
    "Decision",
    "HITLRequest",
    "HITLResponse",
    "PermissionChecker",
    "PermissionMode",
    "Rule",
    "RuleEngine",
    "RuleLayers",
    "RuleSet",
    "TargetInfo",
    "ToolCategory",
    "categorize",
    "extract_target",
    "friendly_name",
    "internal_name",
    "load_rules",
    "match_pattern",
    "resolve_mode",
]
