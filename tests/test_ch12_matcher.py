"""ch12 权限匹配器四类型扩展（spec F1）：exact / glob / regex / not 单测。

防的 bug：
- ch12 前 Pattern 只有 glob 一种形态，`=git status`/`~^npm`/`!rm *` 等前缀语法
  会被当作普通 glob 字面量，既不命中也不报错，用户规则静默失效（AC1-AC3）。
- compile_matcher 空串/非法正则曾抛裸 re.error（不是 ValueError），加载层按
  ValueError 捕获会漏掉，导致整文件崩溃而非跳过该条（F1.4）。
- 反向 not 是"任意其它类型的取反包装"，嵌套解析错（如 ! 后接空串）曾产生
  恒真/恒假匹配，让 deny 规则失效。
- rules.py 改造后若 Rule.pattern 语义变化或 match_target 忘记 evaluate，
  既有 `Bash(git *)` 配置行为会漂移（F1.5 向后兼容）。
"""

from __future__ import annotations

import pytest

from newcode.permission import rules as R
from newcode.permission.matcher import (
    ExactMatcher,
    GlobMatcher,
    NotMatcher,
    RegexMatcher,
    compile_matcher,
    evaluate,
    matcher_from_spec,
)


class TestCompileMatcher:
    """compile_matcher 四种前缀解析（F1.2/F1.3）。"""

    @pytest.mark.parametrize(
        "src,type_name",
        [
            ("=foo", "ExactMatcher"),
            ("~re", "RegexMatcher"),
            ("!foo", "NotMatcher"),
            ("foo", "GlobMatcher"),
        ],
        ids=["exact", "regex", "not", "glob-default"],
    )
    def test_prefix_dispatch(self, src, type_name):
        m = compile_matcher(src)
        assert type(m).__name__ == type_name

    def test_empty_raises(self):
        """空串 → ValueError（而非静默 glob 全匹配，防规则语义漂移）。"""
        with pytest.raises(ValueError):
            compile_matcher("")

    def test_invalid_regex_raises_valueerror(self):
        """未闭合正则 → ValueError（re.error 转为 ValueError，加载层统一捕获）。"""
        with pytest.raises(ValueError):
            compile_matcher("~[invalid")
        with pytest.raises(ValueError):
            compile_matcher("~(")

    def test_str_representations(self):
        """__str__ 供 /hooks 展示与调试（N6）。"""
        assert str(compile_matcher("=foo")) == "=foo"
        assert str(compile_matcher("~re")) == "~re"
        assert str(compile_matcher("!foo")) == "!foo"
        assert str(compile_matcher("foo")) == "foo"


class TestExact:
    def test_exact_whole_string(self):
        """精确整串相等：`=git status` 命中 git status、不命中 git status -s（AC1）。"""
        m = compile_matcher("=git status")
        assert m.match("git status")
        assert not m.match("git status -s")
        assert not m.match(" git status")

    def test_exact_empty_value(self):
        """`=` 空值 = 精确匹配空串（防整串前缀被当 glob 字面量）。"""
        m = compile_matcher("=")
        assert m.match("")
        assert not m.match("x")


class TestGlob:
    def test_command_is_command_true(self):
        """is_command=True：Bash 命令整串通配（防 * 只匹配单段破坏命令语义）。"""
        m = GlobMatcher("git *", is_command=True)
        assert m.match("git status")
        assert m.match("git push origin main")
        assert not m.match("svn status")

    def test_path_recursive_is_command_false(self):
        """is_command=False：路径按 ** 递归（防 * 跨段误匹配）。"""
        m = GlobMatcher("src/**/*.py", is_command=False)
        assert m.match("src/main.py")  # ** 匹配零段
        assert m.match("src/a/b/c.py")
        assert not m.match("lib/main.py")

    def test_glob_auto_detect_no_slash(self):
        """matcher_from_spec 的 glob 不带 is_command：无 / → fnmatch 整串（spec 场景 2）。"""
        m = matcher_from_spec({"type": "glob", "value": "rm -rf *"})
        assert m.match("rm -rf /tmp/x")  # 整串 fnmatch，* 含空格与斜杠

    def test_glob_auto_detect_path(self):
        """matcher_from_spec 的 glob 有 / 或 ** → 路径分段递归。"""
        m = matcher_from_spec({"type": "glob", "value": "**/*.py"})
        assert m.match("a/b/c.py")

    def test_empty_pattern_matches_all(self):
        """空 pattern 恒匹配（防 glob 空串被当作精确空串）。"""
        m = GlobMatcher("")
        assert m.match("anything at all")

    def test_escape_characters(self):
        """转义字符按 fnmatch 语义（防 [] 被当作字面量外的特殊处理）。"""
        m = GlobMatcher("a[bc]d")
        assert m.match("abd")
        assert not m.match("axd")


class TestRegex:
    def test_regex_hit(self):
        """`~^npm (install|test)$` 命中 npm install（AC2）。"""
        m = compile_matcher("~^npm (install|test)$")
        assert m.match("npm install")
        assert m.match("npm test")
        assert not m.match("npm run dev")

    def test_regex_search_partial(self):
        """.search 部分匹配（防 fullmatch 语义误判子串命中）。"""
        m = compile_matcher("~delete")
        assert m.match("please delete the file")
        assert not m.match("please keep")


class TestNot:
    def test_not_exact(self):
        """!=foo：不命中 foo、命中 bar（防反向匹配方向颠倒）。"""
        m = compile_matcher("!=foo")
        assert m.match("bar")
        assert not m.match("foo")

    def test_not_regex(self):
        """!~^rm：不以 rm 起头命中（AC3）。"""
        m = compile_matcher("!~^rm")
        assert m.match("ls -lh")
        assert not m.match("rm -rf .")

    def test_not_glob(self):
        """!git *：not 包 glob（防反向只支持精确类型）。"""
        m = compile_matcher("!git *")
        assert m.match("npm install")
        assert not m.match("git status")

    def test_not_nested_double(self):
        """!!foo = foo（双重取反），防一元取反实现错误。"""
        m = compile_matcher("!!foo")
        assert m.match("foo")


class TestMatcherFromSpec:
    """Hook 条件 YAML → Matcher（F4.4）。"""

    def test_four_types(self):
        assert isinstance(
            matcher_from_spec({"type": "exact", "value": "x"}), ExactMatcher
        )
        assert isinstance(
            matcher_from_spec({"type": "glob", "value": "x"}), GlobMatcher
        )
        assert isinstance(
            matcher_from_spec({"type": "regex", "value": "x"}), RegexMatcher
        )
        assert isinstance(
            matcher_from_spec(
                {"type": "not", "inner": {"type": "exact", "value": "x"}}
            ),
            NotMatcher,
        )

    def test_not_missing_inner_raises(self):
        with pytest.raises(ValueError):
            matcher_from_spec({"type": "not"})

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError):
            matcher_from_spec({"type": "magic", "value": "x"})

    def test_missing_value_raises(self):
        with pytest.raises(ValueError):
            matcher_from_spec({"type": "exact"})

    def test_non_dict_raises(self):
        with pytest.raises(ValueError):
            matcher_from_spec("not-a-dict")

    def test_invalid_regex_raises(self):
        with pytest.raises(ValueError):
            matcher_from_spec({"type": "regex", "value": "[invalid"})


class TestEvaluateAndRulesCompat:
    def test_evaluate_is_match(self):
        """evaluate(spec, target) ≡ spec.match(target)（保持调用点语义统一）。"""
        m = compile_matcher("=git status")
        assert evaluate(m, "git status")
        assert not evaluate(m, "git status -s")

    def test_rules_backward_compat(self):
        """Bash(git *) 无前缀 → Glob 语义，与改造前一致（F1.5/AC4）。"""
        rule = R.Rule.parse("Bash(git *)", "allow", "test")
        assert rule is not None
        assert rule.pattern == "git *"  # pattern 原文保留（既有测试依赖）
        assert rule.match_target("git status")
        assert rule.match_target("git push origin main")
        assert not rule.match_target("svn status")

    def test_rules_empty_paren_match_all(self):
        """Bash() → matcher=None 全匹配（等价旧 pattern=""）。"""
        rule = R.Rule.parse("Bash()", "allow", "test")
        assert rule is not None
        assert rule.pattern == ""
        assert rule.matcher is None
        assert rule.match_target("anything")

    def test_rules_new_prefix_exact(self):
        """权限规则侧也支持 = 前缀（AC1 集成点）。"""
        rule = R.Rule.parse("Bash(=git status)", "allow", "test")
        assert rule is not None
        assert rule.match_target("git status")
        assert not rule.match_target("git status -s")

    def test_build_rule_set_bad_entry_stderr(self, capsys):
        """F1.4：非法正则条目 stderr 打印 `rule "<raw>" parse failed:` 并跳过，其余加载。"""
        rs = R.build_rule_set(["Bash(git status)", "Bash(~[invalid)"], "allow", "t")
        assert len(rs.allow) == 1
        assert rs.match("Bash", "git status") is not None
        err = capsys.readouterr().err
        assert 'rule "Bash(~[invalid)" parse failed:' in err
        assert "跳过非法规则条目" in err  # 兼容既有测试文案

    def test_build_rule_set_format_invalid_stderr(self, capsys):
        """F1.4：格式非法条目（工具名含空格）也打印定位信息并跳过。"""
        rs = R.build_rule_set(["Bad Name(x)", "Bash(git *)"], "allow", "t")
        assert len(rs.allow) == 1
        err = capsys.readouterr().err
        assert 'rule "Bad Name(x)" parse failed:' in err
