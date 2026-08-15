"""L1–L4 权限检查器串联单元测试

背景：PermissionChecker 串联 分类→黑名单→沙箱→规则引擎→模式矩阵，Agent 的唯一
入口。这些测试防的 bug：短路顺序错乱（规则 allow 覆盖黑名单/沙箱）、模式矩阵档位
错配、extract_target 解析失败未安全拒绝、create 无法解析根时崩溃。
"""

from mewcode.permission.checker import (
    PermissionChecker,
    categorize,
    extract_target,
    friendly_name,
    internal_name,
)
from mewcode.permission.modes import PermissionMode, ToolCategory
from mewcode.permission.rules import Rule, RuleLayers
from mewcode.permission.types import Decision
from mewcode.provider.base import ToolCall


def _call(name: str, args: dict | None = None) -> ToolCall:
    return ToolCall(tool_name=name, arguments=args or {})


def _empty_layers() -> RuleLayers:
    return RuleLayers()


def _checker(mode: PermissionMode, layers: RuleLayers | None = None, root: str = "."):
    return PermissionChecker(
        project_root=root, mode=mode, layers=layers or _empty_layers()
    )


class TestCategorize:
    def test_readonly_flag_priority(self):
        # readOnly 属性优先于名字判定
        assert categorize("execute_command", True) == ToolCategory.READONLY

    def test_file_write(self):
        assert categorize("write_file", False) == ToolCategory.FILE_WRITE
        assert categorize("edit_file", False) == ToolCategory.FILE_WRITE

    def test_command_default(self):
        assert categorize("execute_command", False) == ToolCategory.COMMAND

    def test_unknown_tool_command(self):
        # 未知工具按最严的 COMMAND 处理
        assert categorize("mystery_tool", False) == ToolCategory.COMMAND


class TestNameMapping:
    def test_friendly(self):
        assert friendly_name("execute_command") == "Bash"
        assert friendly_name("read_file") == "Read"
        assert friendly_name("write_file") == "Write"
        assert friendly_name("edit_file") == "Edit"
        assert friendly_name("list_files") == "Glob"
        assert friendly_name("search_code") == "Grep"

    def test_internal(self):
        assert internal_name("Bash") == "execute_command"
        assert internal_name("Read") == "read_file"

    def test_unknown_passthrough(self):
        assert friendly_name("mystery") == "mystery"
        assert internal_name("mystery") == "mystery"


class TestExtractTarget:
    def test_file_path(self):
        info = extract_target(_call("read_file", {"path": "src/main.py"}))
        assert info.target == "src/main.py"
        assert info.is_file and info.ok

    def test_file_missing_path_unparsable(self):
        # 缺必填字段 → is_file=True, ok=False → 检查器安全拒绝
        info = extract_target(_call("read_file", {}))
        assert info.is_file and not info.ok

    def test_file_non_str_path(self):
        info = extract_target(_call("write_file", {"path": 123}))
        assert info.is_file and not info.ok

    def test_list_files_default_root(self):
        info = extract_target(_call("list_files", {}))
        assert info.target == "."
        assert info.is_file and info.ok

    def test_grep_cwd(self):
        info = extract_target(_call("search_code", {"cwd": "src"}))
        assert info.target == "src"
        assert info.is_file and info.ok

    def test_bash_command(self):
        info = extract_target(_call("execute_command", {"command": "git status"}))
        assert info.target == "git status"
        assert not info.is_file and info.ok

    def test_bash_missing_command_empty(self):
        # Bash 缺 command → 空串 target，落 Ask（非解析失败）
        info = extract_target(_call("execute_command", {}))
        assert info.target == ""
        assert not info.is_file and info.ok

    def test_non_dict_arguments_ok_false(self):
        # 参数非 dict（无法提取 target）→ ok=False
        tc = _call("read_file", {})
        tc.arguments = "not a dict"
        info = extract_target(tc)
        assert not info.ok

    def test_unknown_tool_not_ok(self):
        info = extract_target(_call("mystery", {"x": 1}))
        assert not info.is_file and not info.ok


class TestCheckShortCircuit:
    def test_blacklist_wins_over_allow_rule(self, tmp_path):
        # AC1：黑名单命中 + 规则 allow → 最终 DENY（黑名单优先不可覆盖）
        layers = _empty_layers()
        layers.project.allow.append(Rule("Bash", "rm *", "allow", "project"))
        c = _checker(PermissionMode.DEFAULT, layers, str(tmp_path))
        r = c.check(_call("execute_command", {"command": "rm -rf /"}), read_only=False)
        assert r.decision == Decision.DENY
        assert "黑名单" in r.reason

    def test_sandbox_wins_over_allow_rule(self, tmp_path):
        # 沙箱越界 + 规则 allow → DENY
        layers = _empty_layers()
        layers.project.allow.append(Rule("Write", "**", "allow", "project"))
        c = _checker(PermissionMode.DEFAULT, layers, str(tmp_path))
        r = c.check(_call("write_file", {"path": "../outside.txt", "content": "x"}))
        assert r.decision == Decision.DENY
        assert "项目目录之外" in r.reason

    def test_unparsable_file_path_denied(self, tmp_path):
        # 文件类参数无法解析 → 安全拒绝（AC 隐含）
        c = _checker(PermissionMode.DEFAULT, _empty_layers(), str(tmp_path))
        r = c.check(_call("read_file", {}))
        assert r.decision == Decision.DENY

    def test_bypass_keeps_blacklist(self, tmp_path):
        # AC2/AC14：bypass 下黑名单仍生效
        c = _checker(PermissionMode.BYPASS, _empty_layers(), str(tmp_path))
        r = c.check(_call("execute_command", {"command": "rm -rf /"}), read_only=False)
        assert r.decision == Decision.DENY

    def test_bypass_keeps_sandbox(self, tmp_path):
        # AC14：bypass 下沙箱仍生效
        c = _checker(PermissionMode.BYPASS, _empty_layers(), str(tmp_path))
        r = c.check(_call("read_file", {"path": "../etc/passwd"}))
        assert r.decision == Decision.DENY


class TestModeMatrix:
    """逐档逐类断言（AC6）"""

    def test_default(self, tmp_path):
        c = _checker(PermissionMode.DEFAULT, _empty_layers(), str(tmp_path))
        assert (
            c.check(_call("read_file", {"path": "a.py"}), read_only=True).decision
            == Decision.ALLOW
        )
        assert c.check(_call("write_file", {"path": "b.py"})).decision == Decision.ASK
        assert (
            c.check(_call("execute_command", {"command": "ls"})).decision
            == Decision.ASK
        )

    def test_accept_edits(self, tmp_path):
        c = _checker(PermissionMode.ACCEPT_EDITS, _empty_layers(), str(tmp_path))
        assert (
            c.check(_call("read_file", {"path": "a.py"}), read_only=True).decision
            == Decision.ALLOW
        )
        assert c.check(_call("write_file", {"path": "b.py"})).decision == Decision.ALLOW
        assert (
            c.check(_call("execute_command", {"command": "ls"})).decision
            == Decision.ASK
        )

    def test_plan_same_as_default(self, tmp_path):
        c = _checker(PermissionMode.PLAN, _empty_layers(), str(tmp_path))
        assert (
            c.check(_call("read_file", {"path": "a.py"}), read_only=True).decision
            == Decision.ALLOW
        )
        assert c.check(_call("write_file", {"path": "b.py"})).decision == Decision.ASK
        assert (
            c.check(_call("execute_command", {"command": "ls"})).decision
            == Decision.ASK
        )

    def test_bypass_all_allow(self, tmp_path):
        c = _checker(PermissionMode.BYPASS, _empty_layers(), str(tmp_path))
        assert (
            c.check(_call("read_file", {"path": "a.py"}), read_only=True).decision
            == Decision.ALLOW
        )
        assert c.check(_call("write_file", {"path": "b.py"})).decision == Decision.ALLOW
        assert (
            c.check(_call("execute_command", {"command": "ls"})).decision
            == Decision.ALLOW
        )

    def test_bypass_skips_rules(self, tmp_path):
        # bypass 跳过规则引擎：deny 规则失效
        layers = _empty_layers()
        layers.project.deny.append(Rule("Bash", "git *", "deny", "project"))
        c = _checker(PermissionMode.BYPASS, layers, str(tmp_path))
        assert (
            c.check(_call("execute_command", {"command": "git push"})).decision
            == Decision.ALLOW
        )

    def test_rule_overrides_mode_matrix(self, tmp_path):
        # 规则优先级 > 模式矩阵（F4）
        layers = _empty_layers()
        layers.local.allow.append(Rule("Bash", "git *", "allow", "local"))
        c = _checker(PermissionMode.DEFAULT, layers, str(tmp_path))
        # 命中规则 → ALLOW 而非模式矩阵的 ASK
        assert (
            c.check(_call("execute_command", {"command": "git push"})).decision
            == Decision.ALLOW
        )


class TestCheckerMisc:
    def test_set_mode_runtime_switch(self, tmp_path):
        c = _checker(PermissionMode.DEFAULT, _empty_layers(), str(tmp_path))
        assert c.mode == PermissionMode.DEFAULT
        c.set_mode(PermissionMode.ACCEPT_EDITS)
        assert c.mode == PermissionMode.ACCEPT_EDITS
        assert c.start_mode == PermissionMode.DEFAULT

    def test_create_unresolvable_root_still_works(self, tmp_path):
        # 无法解析的项目根仍返回非 null 安全引擎（容错）
        c = PermissionChecker.create(str(tmp_path / "no_such_dir"))
        assert c is not None
        assert c.check(_call("read_file", {"path": "x"})).decision in (
            Decision.ALLOW,
            Decision.DENY,
            Decision.ASK,
        )

    def test_readonly_checker_allows_read(self, tmp_path):
        # 只读工具在 default 下自动放行（配合 read_only 标志）
        c = _checker(PermissionMode.DEFAULT, _empty_layers(), str(tmp_path))
        r = c.check(_call("read_file", {"path": "a.py"}), read_only=True)
        assert r.decision == Decision.ALLOW

    def test_persist_local_allow_writes_file(self, tmp_path):
        # HITL「永久放行」写入本地级规则文件（F7）
        c = _checker(PermissionMode.DEFAULT, _empty_layers(), str(tmp_path))
        c.persist_local_allow(_call("execute_command", {"command": "git status"}))
        local = tmp_path / ".mewcode" / "permissions.local.yaml"
        assert local.exists()
        content = local.read_text(encoding="utf-8")
        assert "Bash(git status)" in content
