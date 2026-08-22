"""ch12 条件表达式（spec F4）：all_of/any_of 组合 + 点分路径取值 + 模板替换。

防的 bug：
- field 路径不存在曾抛 KeyError 使 dispatch 崩溃——按 F4.3 应返回 ""（AC18）。
- bool 值未转小写时 `is_error: false` 匹配不上（YAML 直觉是字符串 false，
  但 payload 里是 Python False 对象），格式化动作永久失效（spec 场景 1）。
- 裸 `{}` 或非法模板若抛给调用方，一次格式化失败会让整个 Agent 循环中断——
  按 F4.8 必须返回原文（AC19a）。
- format_map 把 `{tool_input.path}` 当属性访问（str 无 .path）抛 AttributeError，
  曾导致所有嵌套字段模板失效——必须用正则逐组替换。
"""

from __future__ import annotations

from mewcode.hooks.conditions import (
    AtomCondition,
    Condition,
    eval_condition,
    get_by_path,
)
from mewcode.hooks.executor import render_template
from mewcode.hooks.types import CombineMode
from mewcode.permission.matcher import matcher_from_spec


def _matcher(spec_type: str, value: str):
    return matcher_from_spec({"type": spec_type, "value": value})


class TestGetByPath:
    def test_nested_dict(self):
        assert (
            get_by_path({"tool_input": {"path": "a/b.py"}}, "tool_input.path")
            == "a/b.py"
        )

    def test_deep_multi_level(self):
        p = {"a": {"b": {"c": "deep"}}}
        assert get_by_path(p, "a.b.c") == "deep"

    def test_missing_path_returns_empty(self):
        """路径不存在 → ""，不报错（F4.3/AC18）。"""
        assert get_by_path({"tool_input": {}}, "tool_input.path") == ""
        assert get_by_path({}, "anything") == ""

    def test_bool_to_lowercase(self):
        """bool 转 "true"/"false"（spec 场景 1 的 is_error: false 依赖）。"""
        assert get_by_path({"is_error": False}, "is_error") == "false"
        assert get_by_path({"is_error": True}, "is_error") == "true"

    def test_int_float_to_str(self):
        assert get_by_path({"n": 5}, "n") == "5"
        assert get_by_path({"n": 5.5}, "n") == "5.5"

    def test_nested_container_json(self):
        """嵌套 dict/list → json.dumps(sort_keys=True)（与 N5 稳定序列化一致）。"""
        assert get_by_path({"args": {"b": 1, "a": 2}}, "args") == '{"a": 2, "b": 1}'

    def test_none_to_empty(self):
        assert get_by_path({"x": None}, "x") == ""


class TestEvalCondition:
    def test_none_means_unconditional(self):
        """cond=None → True（无条件触发，F4.1）。"""
        assert eval_condition(None, {})

    def test_all_of_all_match(self):
        c = Condition(
            CombineMode.ALL_OF,
            [
                AtomCondition("tool_name", _matcher("exact", "write_file")),
                AtomCondition("tool_input.path", _matcher("glob", "**/*.py")),
            ],
        )
        assert eval_condition(
            c, {"tool_name": "write_file", "tool_input": {"path": "a.py"}}
        )

    def test_all_of_one_mismatch(self):
        c = Condition(
            CombineMode.ALL_OF,
            [
                AtomCondition("tool_name", _matcher("exact", "write_file")),
                AtomCondition("tool_input.path", _matcher("glob", "**/*.py")),
            ],
        )
        assert not eval_condition(
            c, {"tool_name": "read_file", "tool_input": {"path": "a.py"}}
        )

    def test_any_of_one_match(self):
        c = Condition(
            CombineMode.ANY_OF,
            [
                AtomCondition("tool_name", _matcher("exact", "write_file")),
                AtomCondition("tool_input.path", _matcher("glob", "**/*.py")),
            ],
        )
        assert eval_condition(
            c, {"tool_name": "read_file", "tool_input": {"path": "a.py"}}
        )
        assert not eval_condition(
            c, {"tool_name": "read_file", "tool_input": {"path": "a.txt"}}
        )

    def test_empty_atoms(self):
        """all_of 空 → True；any_of 空 → False（标准集合语义）。"""
        assert eval_condition(Condition(CombineMode.ALL_OF, []), {})
        assert not eval_condition(Condition(CombineMode.ANY_OF, []), {})

    def test_missing_field_matches_empty_semantics(self):
        """field 缺失 → "" 参与匹配：exact "" 命中、exact "x" 不命中。"""
        c = Condition(
            CombineMode.ALL_OF,
            [AtomCondition("tool_input.path", _matcher("exact", ""))],
        )
        assert eval_condition(c, {"tool_input": {}})
        c2 = Condition(
            CombineMode.ALL_OF,
            [AtomCondition("tool_input.path", _matcher("exact", "a.py"))],
        )
        assert not eval_condition(c2, {})

    def test_not_in_condition(self):
        """条件也支持 not 类型（F4.4 四种操作符）。"""
        c = Condition(
            CombineMode.ALL_OF,
            [
                AtomCondition(
                    "tool_name",
                    matcher_from_spec(
                        {
                            "type": "not",
                            "inner": {"type": "exact", "value": "write_file"},
                        }
                    ),
                )
            ],
        )
        assert eval_condition(c, {"tool_name": "read_file"})
        assert not eval_condition(c, {"tool_name": "write_file"})


class TestRenderTemplate:
    def test_field_replacement(self):
        assert (
            render_template("x {tool_input.path} y", {"tool_input": {"path": "a.py"}})
            == "x a.py y"
        )

    def test_unknown_field_empty(self):
        """未知字段 → ""（F4.8）。"""
        assert render_template("{missing}", {}) == ""

    def test_bare_braces_returns_original(self):
        """裸 `{}` → 返回原文（F4.8/AC19a）。"""
        assert render_template("echo {}", {}) == "echo {}"

    def test_invalid_template_returns_original(self):
        """非法模板（含 ! 等转换符）→ 返回原文，绝不抛。"""
        assert render_template("echo {x!r}", {}) == "echo {x!r}"

    def test_dot_path_nested(self):
        assert (
            render_template("{tool_input.path}", {"tool_input": {"path": "a/b.py"}})
            == "a/b.py"
        )

    def test_event_tool_name(self):
        """{event}/{tool_name} 映射 $EVENT/$TOOL_NAME 语义（F4.7）。"""
        assert (
            render_template(
                "{event} {tool_name}",
                {"event": "turn_start", "tool_name": "write_file"},
            )
            == "turn_start write_file"
        )

    def test_message_error(self):
        assert (
            render_template("{message}|{error}", {"message": "hi", "error": "boom"})
            == "hi|boom"
        )
