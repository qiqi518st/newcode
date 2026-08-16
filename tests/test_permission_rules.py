"""L3 规则解析 / glob 匹配 / 三层合并 单元测试

背景：ch03 无规则引擎，只靠模式矩阵硬编码。本章引入「工具名(模式)」格式的
allow/deny 规则，按 会话>本地>项目>用户 分层。这些测试防的 bug：
规则解析吃非法串崩溃、glob 通配层级错误、deny/allow 同层命中优先级错乱、
三层合并顺序颠倒、格式错误规则导致整个文件降级。
"""

import os

import pytest
import yaml

from mewcode.permission import rules as R
from mewcode.permission.types import Decision


class TestRuleParse:
    def test_exact_with_pattern(self):
        rule = R.Rule.parse("Bash(git status)", "allow", "test")
        assert rule is not None
        assert rule.tool_name == "Bash"
        assert rule.pattern == "git status"
        assert rule.action == "allow"

    def test_no_paren_matches_all(self):
        rule = R.Rule.parse("Read", "allow", "test")
        assert rule is not None
        assert rule.tool_name == "Read"
        assert rule.pattern == ""

    def test_glob_pattern(self):
        rule = R.Rule.parse("Write(src/**)", "deny", "test")
        assert rule is not None
        assert rule.pattern == "src/**"

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "  ",
            "Bash(git",  # 缺右括号
            "Bash(git)extra",  # 括号后多余
            "mcp__a b__x",  # ch07：工具名含空格（非法字符）
            "mcp__demo__a/b",  # ch07：工具名含 /（LLM 工具名禁用字符）
            "Bad Name(git)",  # 工具名含空格
        ],
    )
    def test_invalid_returns_none(self, raw):
        assert R.Rule.parse(raw, "allow", "test") is None

    def test_unknown_but_legal_name_now_parses(self):
        """ch07 泛化：任意合法字符工具名（含未知名/小写）均可解析。

        防回归背景：正则放宽前只认 6 个内置友好名，Foo/bash 被拒；
        泛化后为支持 mcp__ 前缀接受 [A-Za-z0-9_-]+（spec F12）。
        未知名规则只是永不命中，无害。
        """
        rule = R.Rule.parse("Foo(git)", "allow", "test")
        assert rule is not None and rule.tool_name == "Foo"
        rule = R.Rule.parse("bash(git)", "allow", "test")
        assert rule is not None and rule.tool_name == "bash"

    def test_empty_paren_means_match_all(self):
        # Bash() 空模式 → pattern="" → 匹配全部，与无括号等价
        rule = R.Rule.parse("Bash()", "allow", "test")
        assert rule is not None
        assert rule.pattern == ""
        assert rule.match_target("git status")

    def test_non_str_returns_none(self):
        assert R.Rule.parse(123, "allow", "test") is None


class TestMatchPattern:
    def test_empty_pattern_matches_all(self):
        assert R.match_pattern("", "anything at all")

    def test_command_exact(self):
        assert R.match_pattern("git status", "git status")
        assert not R.match_pattern("git status", "git push")

    def test_command_glob(self):
        assert R.match_pattern("git *", "git status")
        assert R.match_pattern("git *", "git push origin main")
        assert not R.match_pattern("git *", "svn status")

    def test_path_single_segment_glob(self):
        # 防的 bug：* 跨段匹配导致 src/main.py 误匹配 src/a/b.py
        assert R.match_pattern("src/*.py", "src/main.py")
        assert not R.match_pattern("src/*.py", "src/sub/main.py")

    def test_path_double_star_recursive(self):
        assert R.match_pattern("src/**/*.py", "src/a/b/c.py")
        assert R.match_pattern("src/**/*.py", "src/main.py")  # ** 匹配零段
        assert not R.match_pattern("src/**/*.py", "lib/main.py")

    def test_path_double_star_dir(self):
        assert R.match_pattern("src/**", "src/a/b/c")
        assert R.match_pattern("src/**", "src")


class TestRuleSet:
    def test_deny_first_wins(self):
        # 防的 bug：同层 allow 先命中使 deny 规则失效
        rs = R.RuleSet()
        rs.allow.append(R.Rule("Bash", "rm *", "allow", "t"))
        rs.deny.append(R.Rule("Bash", "rm -rf *", "deny", "t"))
        assert rs.match("Bash", "rm -rf /") == Decision.DENY
        assert rs.match("Bash", "rm file.txt") == Decision.ALLOW

    def test_no_match_returns_none(self):
        rs = R.RuleSet()
        rs.allow.append(R.Rule("Read", "*.py", "allow", "t"))
        assert rs.match("Bash", "x") is None
        assert rs.match("Read", "main.txt") is None

    def test_wrong_tool_ignored(self):
        rs = R.RuleSet()
        rs.allow.append(R.Rule("Read", "*.py", "allow", "t"))
        assert rs.match("Bash", "main.py") is None


class TestRuleLayers:
    def test_local_overrides_project(self):
        # 防的 bug：跨层顺序颠倒，项目/用户级覆盖本地级
        layers = R.RuleLayers()
        layers.local.deny.append(R.Rule("Bash", "git *", "deny", "local"))
        layers.project.allow.append(R.Rule("Bash", "git *", "allow", "project"))
        assert layers.match("Bash", "git status") == Decision.DENY

    def test_project_overrides_user(self):
        layers = R.RuleLayers()
        layers.project.deny.append(R.Rule("Write", "**", "deny", "project"))
        layers.user.allow.append(R.Rule("Write", "**", "allow", "user"))
        assert layers.match("Write", "x.txt") == Decision.DENY

    def test_empty_layer_falls_through(self):
        layers = R.RuleLayers()
        layers.user.allow.append(R.Rule("Read", "*.py", "allow", "user"))
        assert layers.match("Read", "a.py") == Decision.ALLOW

    def test_no_match_returns_none(self):
        layers = R.RuleLayers()
        layers.user.allow.append(R.Rule("Read", "*.py", "allow", "user"))
        assert layers.match("Read", "a.txt") is None


class TestMcpToolRules:
    """ch07：mcp__ 前缀工具名的规则解析与通配匹配（spec F12/AC11）。

    防的 bug：泛化前正则只认 6 个内置名，mcp__ 规则被静默跳过；
    RuleSet.match 用 == 比对使 mcp__github__* 裸通配规则永不命中。
    """

    def test_parse_exact_mcp_name(self):
        rule = R.Rule.parse("mcp__github__create_issue", "allow", "test")
        assert rule is not None
        assert rule.tool_name == "mcp__github__create_issue"
        assert rule.pattern == ""  # 无括号 -> 匹配该工具全部调用

    def test_parse_mcp_name_with_parens(self):
        rule = R.Rule.parse("mcp__fs__read_file(/tmp/x)", "deny", "test")
        assert rule is not None
        assert rule.tool_name == "mcp__fs__read_file"
        assert rule.pattern == "/tmp/x"

    def test_bare_wildcard_matches_same_server_tools(self):
        # 防的 bug：mcp__github__* 曾无法匹配任何工具（== 比对）
        rs = R.RuleSet()
        rs.allow.append(R.Rule("mcp__github__*", "", "allow", "t"))
        assert rs.match("mcp__github__create_issue", "") == Decision.ALLOW
        assert rs.match("mcp__github__search", "") == Decision.ALLOW
        assert rs.match("mcp__other__create_issue", "") is None

    def test_exact_mcp_name_no_cross_tool_leak(self):
        rs = R.RuleSet()
        rs.allow.append(R.Rule("mcp__github__create_issue", "", "allow", "t"))
        assert rs.match("mcp__github__delete_repo", "") is None

    def test_builtin_rules_unaffected_by_wildcard_branch(self):
        # 内置名规则不含 *，仍走精确分支（防泛化破坏既有行为）
        rs = R.RuleSet()
        rs.allow.append(R.Rule("Bash", "git *", "allow", "t"))
        assert rs.match("Bash", "git status") == Decision.ALLOW
        assert rs.match("Read", "git status") is None


class TestLoadRules:
    def _write(self, path, data: dict):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)

    def test_missing_all_files_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(R, "RULE_FILE_USER", str(tmp_path / "user.yaml"))
        layers = R.load_rules(str(tmp_path))
        assert layers.match("Bash", "anything") is None

    def test_load_three_tiers(self, tmp_path, monkeypatch):
        user = tmp_path / "user.yaml"
        self._write(
            user,
            {"permissions": {"allow": ["Bash(git *)"], "deny": ["Write(**/*.log)"]}},
        )
        project = tmp_path / ".mewcode" / "permissions.yaml"
        self._write(
            project,
            {"permissions": {"deny": ["Bash(git push *)"]}},
        )
        local = tmp_path / ".mewcode" / "permissions.local.yaml"
        self._write(local, {"permissions": {"allow": ["Bash(git push origin main)"]}})

        monkeypatch.setattr(R, "RULE_FILE_USER", str(user))
        layers = R.load_rules(str(tmp_path))
        # 本地级 allow 覆盖项目级 deny
        assert layers.match("Bash", "git push origin main") == Decision.ALLOW
        # 项目级 deny 命中其他 push
        assert layers.match("Bash", "git push origin dev") == Decision.DENY
        # 用户级 allow
        assert layers.match("Bash", "git status") == Decision.ALLOW
        # 用户级 deny 的 glob
        assert layers.match("Write", "build/app.log") == Decision.DENY

    def test_invalid_yaml_degrades_gracefully(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(R, "RULE_FILE_USER", str(tmp_path / "user.yaml"))
        bad = tmp_path / ".mewcode" / "permissions.yaml"
        os.makedirs(bad.parent, exist_ok=True)
        bad.write_text(": : bad yaml :\n\t", encoding="utf-8")
        layers = R.load_rules(str(tmp_path))
        # 防的 bug：非法 YAML 导致整个加载流程崩溃
        assert layers.match("Bash", "anything") is None
        out = capsys.readouterr()
        assert "警告" in out.err

    def test_invalid_rule_skipped(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(R, "RULE_FILE_USER", str(tmp_path / "user.yaml"))
        project = tmp_path / ".mewcode" / "permissions.yaml"
        self._write(
            project,
            {
                "permissions": {
                    # "Bad Name(x)" 含空格 -> 非法名；42 非字符串
                    "allow": ["Bash(git status)", "Bad Name(x)", 42]
                }
            },
        )
        layers = R.load_rules(str(tmp_path))
        # 合法规则仍生效，非法条目跳过
        assert layers.match("Bash", "git status") == Decision.ALLOW
        out = capsys.readouterr()
        assert "跳过非法规则条目" in out.err

    def test_load_settings_nonexistent_returns_empty(self, tmp_path):
        assert R.load_settings(str(tmp_path / "nope.yaml")) == {}
