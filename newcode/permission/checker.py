"""权限检查器串联入口（L1–L4 流水线 + 参数提取 + 工具分类）

流水线：
① 分类 → extract_target → 黑名单(仅COMMAND+非空)
② 沙箱(仅文件类，ok==False → Deny)
③ 规则引擎(local → project → user)
④ 模式兜底 → Allow/Ask
bypassPermissions 跳过 ③④（规则引擎和权限模式；HITL 由Agent层跳过）
"""

import os
import re
import sys
from typing import TYPE_CHECKING

import yaml

from .blocklist import hits_blacklist
from .engine import RuleEngine
from .modes import PermissionMode, ToolCategory, resolve_mode
from .rules import (
    RULE_FILE_LOCAL,
    Rule,
    RuleLayers,
    RuleSet,
    load_rules,
    load_settings,
)
from .sandbox import check_path, resolve_root
from .types import CheckResult, Decision, TargetInfo

if TYPE_CHECKING:
    from ..provider.base import ToolCall

# 内部名 → 友好名映射
_FRIENDLY_NAME_MAP: dict[str, str] = {
    "Bash": "execute_command",
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "edit_file",
    "Glob": "list_files",
    "Grep": "search_code",
}

# 反向映射
_INTERNAL_TO_FRIENDLY: dict[str, str] = {v: k for k, v in _FRIENDLY_NAME_MAP.items()}


def friendly_name(internal: str) -> str:
    """内部名 → 友好名；未知原样返回"""
    return _INTERNAL_TO_FRIENDLY.get(internal, internal)


def internal_name(friendly: str) -> str:
    """友好名 → 内部名；未知原样返回"""
    return _FRIENDLY_NAME_MAP.get(friendly, friendly)


def categorize(internal: str, read_only: bool) -> ToolCategory:
    """工具分类：read_only 属性优先于名字判定"""
    if read_only:
        return ToolCategory.READONLY
    if internal == "write_memory":
        # 记忆写入只写记忆命名空间（内部经 MemoryManager 校验），四档全 ALLOW
        return ToolCategory.MEMORY
    if internal in ("write_file", "edit_file"):
        return ToolCategory.FILE_WRITE
    # 其余（含 Bash、未知工具）→ COMMAND（最严）
    return ToolCategory.COMMAND


def extract_target(tool_call: "ToolCall") -> TargetInfo:
    """从 ToolCall 中提取匹配目标，解析 arguments 参数字典"""
    args = tool_call.arguments
    if not isinstance(args, dict):
        return TargetInfo("", False, False)

    internal = tool_call.tool_name

    # 文件类工具：取 path
    if internal in ("read_file", "write_file", "edit_file"):
        path = args.get("path")
        if path is None or not isinstance(path, str):
            return TargetInfo("", True, False)  # 缺必填字段
        return TargetInfo(path, True, True)

    # glob / grep：取 path（搜索根目录），空→"."
    if internal in ("list_files", "search_code"):
        path = args.get("cwd", args.get("path", ""))
        if isinstance(path, str) and path:
            return TargetInfo(path, True, True)
        return TargetInfo(".", True, True)

    # Bash：取 command
    if internal == "execute_command":
        command = args.get("command")
        if command is None or not isinstance(command, str):
            return TargetInfo("", False, True)  # 缺 command 视为空串
        return TargetInfo(command, False, True)

    # 未知工具
    return TargetInfo("", False, False)


def _escape_glob(s: str) -> str:
    """转义字符串中的 glob 特殊字符，防止被当通配符"""
    return re.sub(r"([*?\[\]])", r"[\1]", s)


def get_default_mode(project_root: str) -> PermissionMode:
    """从三层配置中读取 defaultMode，按 local > project > user 优先级"""
    local_path = os.path.join(project_root, RULE_FILE_LOCAL)
    project_path = os.path.join(project_root, ".newcode/permissions.yaml")
    user_path = os.path.expanduser("~/.config/newcode/permissions.yaml")

    for path in [local_path, project_path, user_path]:
        settings = load_settings(path)
        if isinstance(settings, dict):
            mode_str = settings.get("defaultMode")
            if mode_str and isinstance(mode_str, str):
                parsed = PermissionMode.parse(mode_str)
                if parsed is not None:
                    return parsed
    return PermissionMode.DEFAULT


class PermissionChecker:
    """权限检查器：串联前四层防线，Agent 调用的唯一入口"""

    def __init__(
        self,
        project_root: str,
        mode: PermissionMode,
        layers: RuleLayers,
    ) -> None:
        self._root = project_root
        self._mode = mode
        self._start_mode = mode
        self._layers = layers
        self._engine = RuleEngine(layers)

    # ── ch10：/permission_* 命令支撑（T0a）────────────────────────

    def _local_rules_path(self) -> str:
        """本地规则文件路径（项目根下 .newcode/permissions.local.yaml）。"""
        return os.path.join(self._root, RULE_FILE_LOCAL)

    def count_rules(self) -> int:
        """统计三层规则文件（local/project/user）当前生效的规则总数。"""
        total = 0
        for layer in (self._layers.local, self._layers.project, self._layers.user):
            total += len(layer.allow) + len(layer.deny)
        return total

    def add_rule(self, pattern: str, effect: str = "allow") -> None:
        """写入一条本地规则（镜像 persist_local_allow 的写回路径），并同步内存层立即生效。

        pattern 为用户提供的规则字面量（如 "Bash(git *)" 或 "Read"），不做转义—
        与 persist_local_allow 不同，这里输入已是规则格式。effect 限 "allow"/"deny"。
        """
        action = "allow" if str(effect) == "allow" else "deny"
        local_path = self._local_rules_path()
        settings = load_settings(local_path)
        if not isinstance(settings, dict):
            settings = {}
        permissions = settings.get("permissions", {})
        if not isinstance(permissions, dict):
            permissions = {}
        rules = permissions.get(action, [])
        if not isinstance(rules, list):
            rules = []
        if str(pattern) not in rules:
            rules.append(str(pattern))
            permissions[action] = rules
            settings["permissions"] = permissions
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    settings, f, allow_unicode=True, default_flow_style=False
                )
        # 内存同步：让新规则对后续 check() 立即生效
        # ch12（F1.4）：匹配描述编译失败（如 Bad Name(~[invalid)）抛 ValueError → 仅不生效
        try:
            rule = Rule.parse(str(pattern), action, local_path)
        except ValueError:
            rule = None
        if rule is not None:
            if action == "allow":
                target = self._layers.local.allow
            else:
                target = self._layers.local.deny
            if rule not in target:
                target.append(rule)

    def reset_rules(self) -> int:
        """清空本地规则（仅清 allow/deny，保留 defaultMode 等其它配置），返回删除条数。"""
        local_path = self._local_rules_path()
        if not os.path.exists(local_path):
            return 0
        settings = load_settings(local_path)
        removed = 0
        if isinstance(settings, dict):
            permissions = settings.get("permissions", {})
            if isinstance(permissions, dict):
                for key in ("allow", "deny"):
                    lst = permissions.get(key)
                    if isinstance(lst, list):
                        removed += len(lst)
                permissions["allow"] = []
                permissions["deny"] = []
                settings["permissions"] = permissions
                with open(local_path, "w", encoding="utf-8") as f:
                    yaml.safe_dump(
                        settings, f, allow_unicode=True, default_flow_style=False
                    )
        # 内存同步清空本地层（复用同一 RuleLayers 对象，RuleEngine 立即看到）
        self._layers.local = RuleSet()
        return removed

    @staticmethod
    def create(project_root: str) -> "PermissionChecker":
        """工厂方法：解析项目根、加载三层配置、确定启动模式。
        即使致命错也返回非 null 空规则安全引擎 + stderr 警告；
        配置格式错误只降级对应文件，不抛致命异常。
        """
        root = resolve_root(project_root)
        if not os.path.exists(root):
            print(f"警告: 项目根目录不存在或无法解析: {root}", file=sys.stderr)

        layers = load_rules(project_root)
        mode = get_default_mode(project_root)

        return PermissionChecker(
            project_root=root,
            mode=mode,
            layers=layers,
        )

    def check(
        self,
        tool_call: "ToolCall",
        is_interactive: bool = True,
        read_only: bool = False,
    ) -> CheckResult:
        """前四层流水线：
        ① 分类 → extract_target → 黑名单(仅COMMAND+非空)
        ② 沙箱(仅文件类，ok==False → Deny)
        ③ 规则引擎(local → project → user)
        ④ 模式兜底 → Allow/Ask
        bypassPermissions 跳过 ③④（规则引擎和权限模式；HITL 由Agent层跳过）
        """
        # ① 分类 + 提取 target
        cat = categorize(tool_call.tool_name, read_only)
        info = extract_target(tool_call)

        # ② 黑名单（仅 COMMAND + 非空）
        if cat == ToolCategory.COMMAND and info.target and hits_blacklist(info.target):
            return CheckResult(
                decision=Decision.DENY,
                reason=f"命中危险命令黑名单：{info.target[:80]}",
            )

        # ③ 沙箱（仅文件类）
        if info.is_file:
            if not info.ok:
                return CheckResult(
                    decision=Decision.DENY,
                    reason="无法解析文件路径参数，安全拒绝",
                )
            sandbox_ok, _ = check_path(info.target, self._root)
            if not sandbox_ok:
                return CheckResult(
                    decision=Decision.DENY,
                    reason=f"路径在项目目录之外：{info.target}",
                )

        # bypassPermissions 跳过规则引擎和模式兜底
        if self._mode == PermissionMode.BYPASS:
            return CheckResult(decision=Decision.ALLOW, reason="")

        # ④ 规则引擎
        fn = friendly_name(tool_call.tool_name)
        rule_result = self._engine.match(fn, info.target)
        if rule_result is not None:
            return rule_result

        # ⑤ 模式兜底
        decision = resolve_mode(self._mode, cat)
        if decision == Decision.ASK:
            return CheckResult(
                decision=Decision.ASK,
                reason=f"{self._mode.display_name()} 模式下 {cat.value} 类操作需确认",
            )
        return CheckResult(decision=Decision.ALLOW, reason="")

    def for_subagent(
        self, mode: PermissionMode, *, root: str | None = None
    ) -> "PermissionChecker":
        """构造共享规则层的子 Agent 检查器（ch13 F4.2/F5.3，A2）。

        - **复用父实例的 _layers**：父对话 persist_local_allow 过的精确规则，子 Agent
          同样命中（用户已批准过的不再重问，A2）
        - mode 换为子角色模式（独立；子 Agent 自身的模式不改变主 Agent 模式，F4.1）
        - 子 Agent 永不触发 HITL：is_interactive=False 由 Agent 层保证（B1）
        - ch14：root 覆盖沙箱根（worktree 隔离子 Agent 的权限沙箱 = worktree 路径，
          F4.4「沙箱根跟随工作目录」；绝对路径出 worktree 即拒）
        """
        return PermissionChecker(
            project_root=root or self._root, mode=mode, layers=self._layers
        )

    def set_mode(self, mode: PermissionMode) -> None:
        """运行时切换权限模式"""
        self._mode = mode

    def persist_local_allow(self, tool_call: "ToolCall") -> None:
        """人在回路「永久」调用：写入本地级规则文件。

        - 生成精确规则（无通配）
        - Bash 命令经 escape_glob 转义
        - 去重后写入
        """
        local_path = os.path.join(self._root, RULE_FILE_LOCAL)
        fn = friendly_name(tool_call.tool_name)

        # 生成精确规则字符串
        if tool_call.tool_name.startswith("mcp__"):
            # ch07：MCP 工具 extract_target 返回 ok=False（无 target 语义），
            # 落盘裸工具名精确规则（无括号，匹配该工具全部调用）（spec F12）
            rule_str = fn
        else:
            info = extract_target(tool_call)
            if not info.ok:
                return
            if info.is_file:
                rule_str = f"{fn}({info.target})"
            else:
                # Bash 命令：转义 glob 特殊字符
                escaped = _escape_glob(info.target)
                rule_str = f"{fn}({escaped})"

        # 读现有配置
        settings = load_settings(local_path)
        if not isinstance(settings, dict):
            settings = {}
        permissions = settings.get("permissions", {})
        if not isinstance(permissions, dict):
            permissions = {}
        allow_list = permissions.get("allow", [])
        if not isinstance(allow_list, list):
            allow_list = []

        # 去重追加
        if rule_str not in allow_list:
            allow_list.append(rule_str)

        permissions["allow"] = allow_list
        settings["permissions"] = permissions

        # 确保目录存在
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        # 写回
        with open(local_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(settings, f, allow_unicode=True, default_flow_style=False)

    @property
    def mode(self) -> PermissionMode:
        return self._mode

    @property
    def start_mode(self) -> PermissionMode:
        return self._start_mode
