"""规则解析、加载、三层合并、glob 匹配（L3）

规则格式：工具名(模式) 如 Bash(git *) 表示所有 git 开头的命令
工具名使用友好名：Bash / Read / Write / Edit / Glob / Grep
"""

import fnmatch
import os
import re
from dataclasses import dataclass
from typing import Literal

import yaml

from .types import Decision

# 规则文件路径常量
RULE_FILE_USER = os.path.expanduser("~/.config/mewcode/permissions.yaml")
RULE_FILE_PROJECT = ".mewcode/permissions.yaml"
RULE_FILE_LOCAL = ".mewcode/permissions.local.yaml"

# 解析规则字符串的正则：提取工具名和括号内模式。
# ch07 泛化：接受任意合法工具名字符集 [A-Za-z0-9_-]+（含 mcp__ 前缀），
# 仍拒绝含空格/特殊字符的非法名。内置 6 个友好名是它的子集，行为不变。
_RULE_PARSE_RE = re.compile(r"^([A-Za-z0-9_-]+)(?:\((.*)\))?$")


@dataclass
class Rule:
    """单条权限规则"""

    tool_name: str  # 友好名：Bash / Read / Write / Edit / Glob / Grep
    pattern: str  # 匹配模式（"" 表示匹配该工具全部调用）
    action: Literal["allow", "deny"]
    source: str  # 来源文件路径（用于调试和反馈）

    @staticmethod
    def parse(raw: str, action: Literal["allow", "deny"], source: str) -> "Rule | None":
        """解析 'Bash(git *)' 或 'Read'；非法返回 None"""
        if not raw or not isinstance(raw, str):
            return None
        raw = raw.strip()
        m = _RULE_PARSE_RE.match(raw)
        if not m:
            return None
        tool_name = m.group(1)
        pattern = m.group(2) or ""  # 无括号→pattern=""（匹配所有）
        return Rule(tool_name=tool_name, pattern=pattern, action=action, source=source)

    def match_target(self, target: str) -> bool:
        """检查此规则是否匹配给定的目标参数"""
        return match_pattern(self.pattern, target)


def match_pattern(pattern: str, target: str) -> bool:
    """glob 匹配：
    - pattern == "" → 恒匹配
    - 命令（Bash 类，不含 /）：* 匹配任意字符（含空格）
    - 文件路径（含 /）：按 / 分段，* 匹配单段内任意字符，** 匹配任意多段
    """
    if pattern == "":
        return True
    # 含路径分隔符 → 按段匹配，保证 * 单层语义（spec F4），** 递归
    if "/" in pattern or "**" in pattern:
        return _match_recursive(pattern, target)
    return fnmatch.fnmatch(target, pattern)


def _match_recursive(pattern: str, target: str) -> bool:
    """支持 ** 递归通配的匹配"""
    # 将 pattern 按 / 分段
    pat_parts = pattern.replace("\\", "/").split("/")
    tgt_parts = target.replace("\\", "/").split("/")

    return _match_parts(pat_parts, tgt_parts)


def _match_parts(pat_parts: list[str], tgt_parts: list[str]) -> bool:
    """递归匹配路径段"""
    if not pat_parts:
        return not tgt_parts
    if pat_parts[0] == "**":
        if len(pat_parts) == 1:
            return True  # ** 匹配一切
        # ** 匹配零段或多段
        for i in range(len(tgt_parts) + 1):
            if _match_parts(pat_parts[1:], tgt_parts[i:]):
                return True
        return False
    if not tgt_parts:
        return False
    if fnmatch.fnmatch(tgt_parts[0], pat_parts[0]):
        return _match_parts(pat_parts[1:], tgt_parts[1:])
    return False


def _tool_name_matches(rule_name: str, friendly: str) -> bool:
    """工具名匹配：规则名含 * 时按 fnmatch 通配（如 mcp__github__* 匹配
    mcp__github__create_issue），否则精确相等（ch07 泛化，spec F12）。
    内置 6 个友好名规则不含 *，走精确分支，行为不变。
    """
    if "*" in rule_name:
        return fnmatch.fnmatchcase(friendly, rule_name)
    return rule_name == friendly


class RuleSet:
    """单层规则集，维护 allow/deny 两个列表"""

    def __init__(self) -> None:
        self.allow: list[Rule] = []
        self.deny: list[Rule] = []

    def match(self, friendly: str, target: str) -> Decision | None:
        """同层内先 deny 后 allow 遍历；命中 deny → DENY，命中 allow → ALLOW，未命中 → None"""
        for rule in self.deny:
            if _tool_name_matches(rule.tool_name, friendly) and rule.match_target(
                target
            ):
                return Decision.DENY
        for rule in self.allow:
            if _tool_name_matches(rule.tool_name, friendly) and rule.match_target(
                target
            ):
                return Decision.ALLOW
        return None


class RuleLayers:
    """三层规则：本地 > 项目 > 用户，跨层先命中定案"""

    def __init__(self) -> None:
        self.local: RuleSet = RuleSet()
        self.project: RuleSet = RuleSet()
        self.user: RuleSet = RuleSet()

    def match(self, friendly: str, target: str) -> Decision | None:
        """local → project → user 顺序，首命中即返回"""
        for layer_name, layer in [
            ("local", self.local),
            ("project", self.project),
            ("user", self.user),
        ]:
            result = layer.match(friendly, target)
            if result is not None:
                return result
        return None


def load_settings(filepath: str) -> dict:
    """加载单个 YAML 文件；文件不存在→{}；格式错误→{} + 打印警告"""
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        import sys

        print(f"警告: 权限规则文件格式错误 ({filepath}): {e}", file=sys.stderr)
        return {}
    except OSError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def build_rule_set(
    entries: list[str], action: Literal["allow", "deny"], source: str
) -> RuleSet:
    """从 YAML 的 allow/deny 列表构建 RuleSet，跳过非法条目"""
    rs = RuleSet()
    for entry in entries:
        if not isinstance(entry, str):
            continue
        rule = Rule.parse(entry, action, source)
        if rule is None:
            import sys

            print(f"警告: 跳过非法规则条目 ({source}): {entry}", file=sys.stderr)
            continue
        if action == "allow":
            rs.allow.append(rule)
        else:
            rs.deny.append(rule)
    return rs


def load_rules(project_root: str) -> RuleLayers:
    """加载三层规则文件，任一缺失/格式错误→降级为空规则集"""
    layers = RuleLayers()

    # 用户级（最低优先级）
    user_settings = load_settings(RULE_FILE_USER)
    user_perms = (
        user_settings.get("permissions", {}) if isinstance(user_settings, dict) else {}
    )
    if isinstance(user_perms, dict):
        layers.user = build_rule_set(
            user_perms.get("allow", []), "allow", RULE_FILE_USER
        )
        user_deny = build_rule_set(user_perms.get("deny", []), "deny", RULE_FILE_USER)
        layers.user.deny.extend(user_deny.deny)

    # 项目级
    project_path = os.path.join(project_root, RULE_FILE_PROJECT)
    project_settings = load_settings(project_path)
    project_perms = (
        project_settings.get("permissions", {})
        if isinstance(project_settings, dict)
        else {}
    )
    if isinstance(project_perms, dict):
        layers.project = build_rule_set(
            project_perms.get("allow", []), "allow", project_path
        )
        proj_deny = build_rule_set(project_perms.get("deny", []), "deny", project_path)
        layers.project.deny.extend(proj_deny.deny)

    # 本地级（最高优先级）
    local_path = os.path.join(project_root, RULE_FILE_LOCAL)
    local_settings = load_settings(local_path)
    local_perms = (
        local_settings.get("permissions", {})
        if isinstance(local_settings, dict)
        else {}
    )
    if isinstance(local_perms, dict):
        layers.local = build_rule_set(local_perms.get("allow", []), "allow", local_path)
        local_deny = build_rule_set(local_perms.get("deny", []), "deny", local_path)
        layers.local.deny.extend(local_deny.deny)

    return layers


def get_default_mode_from_rules(project_root: str) -> str | None:
    """从三层配置中读取 defaultMode，按 local > project > user 优先级"""
    local_path = os.path.join(project_root, RULE_FILE_LOCAL)
    project_path = os.path.join(project_root, RULE_FILE_PROJECT)

    for path in [local_path, project_path, RULE_FILE_USER]:
        settings = load_settings(path)
        if isinstance(settings, dict):
            mode = settings.get("defaultMode")
            if mode and isinstance(mode, str):
                return mode
    return None
