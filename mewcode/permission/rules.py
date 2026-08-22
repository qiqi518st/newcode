"""规则解析、加载、三层合并、匹配（L3）

规则格式：工具名(模式) 如 Bash(git *) 表示所有 git 开头的命令
工具名使用友好名：Bash / Read / Write / Edit / Glob / Grep

ch12（F1）：Pattern 形态从单一字符串扩展为结构化 Matcher（exact/glob/regex/not），
glob 求值逻辑移入 matcher.py 供权限规则与 Hook 条件共用；本模块 re-export match_pattern。
"""

import fnmatch
import os
import re
from dataclasses import dataclass, field
from typing import Literal

import yaml

from .matcher import Matcher, compile_matcher, evaluate, match_pattern
from .types import Decision

# 向后兼容 re-export：ch12 前 glob 求值在本模块，既有调用/测试用 R.match_pattern。
# 列入 __all__ 使 ruff 将其视为公开 re-export（F401 不报）。
__all__ = [
    "RULE_FILE_LOCAL",
    "RULE_FILE_PROJECT",
    "RULE_FILE_USER",
    "Rule",
    "RuleLayers",
    "RuleSet",
    "build_rule_set",
    "get_default_mode_from_rules",
    "load_rules",
    "load_settings",
    "match_pattern",
]

# 规则文件路径常量
RULE_FILE_USER = os.path.expanduser("~/.config/mewcode/permissions.yaml")
RULE_FILE_PROJECT = ".mewcode/permissions.yaml"
RULE_FILE_LOCAL = ".mewcode/permissions.local.yaml"

# 解析规则字符串的正则：提取工具名和括号内模式。
# ch07 泛化：接受任意合法工具名字符集，并允许权限规则工具名使用 * 通配符
# （如 mcp__github__*）；仍拒绝空格、斜杠等非法字符。内置 6 个友好名是其子集。
_RULE_PARSE_RE = re.compile(r"^([A-Za-z0-9_*-]+)(?:\((.*)\))?$")


@dataclass
class Rule:
    """单条权限规则"""

    tool_name: str  # 友好名：Bash / Read / Write / Edit / Glob / Grep
    pattern: str  # 匹配模式原文（"" 表示匹配该工具全部调用）
    action: Literal["allow", "deny"]
    source: str  # 来源文件路径（用于调试和反馈）
    # ch12（F1）：结构化匹配器（None = 匹配全部，等价 Bash(*)）；
    # 从 pattern + tool_name 派生，不在构造参数中显式传入。
    matcher: Matcher | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        # pattern 空 → matcher=None（匹配全部）；否则按前缀语法编译。
        # 非法前缀/正则会抛 ValueError，由调用方（Rule.parse 上游 build_rule_set）按 F1.4 处理。
        if self.pattern == "":
            self.matcher = None
        else:
            self.matcher = compile_matcher(
                self.pattern, is_command=(self.tool_name == "Bash")
            )

    @staticmethod
    def parse(raw: str, action: Literal["allow", "deny"], source: str) -> "Rule | None":
        """解析 'Bash(git *)' 或 'Read'；格式非法返回 None，匹配器编译失败抛 ValueError。

        两种失败区分：正则提取失败（如工具名含空格、缺右括号）→ None；
        匹配描述编译失败（如 Bash(~[invalid) 未闭合正则）→ ValueError（F1.4 需要原因）。
        """
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
        """检查此规则是否匹配给定的目标参数（matcher=None → 恒匹配）"""
        if self.matcher is None:
            return True
        return evaluate(self.matcher, target)


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
    """从 YAML 的 allow/deny 列表构建 RuleSet，跳过非法条目（F1.4）。

    解析失败不再静默跳过：stderr 打印失败规则与原因，其余规则正常加载。
    """
    import sys

    rs = RuleSet()
    for entry in entries:
        if not isinstance(entry, str):
            continue
        try:
            rule = Rule.parse(entry, action, source)
        except ValueError as e:
            # ch12（F1.4）：匹配描述编译失败（如 Bash(~[invalid)）——带原因定位
            print(
                f'rule "{entry}" parse failed: {e}（跳过非法规则条目, {source}）',
                file=sys.stderr,
            )
            continue
        if rule is None:
            # 规则格式非法（工具名含非法字符、缺括号等）——保持原有提示文案
            print(
                f'rule "{entry}" parse failed: 规则格式非法（跳过非法规则条目, {source}）',
                file=sys.stderr,
            )
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
