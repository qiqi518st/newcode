"""ch12 三层配置加载与校验（spec F6）：fail-soft 单测。

防的 bug：
- 三层优先级颠倒会让高优先级层被低优先级层覆盖（F6.2）。
- 单条 hook 校验失败若抛异常会让整个文件/进程崩溃——按 F6.6 应 stderr 定位并跳过。
- all_of + any_of 同时出现应报错（F4.1 互斥，AC17），漏检会让条件语义二义。
- async 用在拦截事件上会丢失拦截信号（F2.2/AC10），加载期必须拒绝。
- timeout 格式非法若静默回退默认值，用户以为 5m 生效实则 30s，超时语义错乱。
- 同名 hook 冲突应保留高优先级层（F6.4/AC13），后到者静默加载会让两个规则打架。
"""

from __future__ import annotations

import os

import pytest

from newcode.hooks import loader as L


def _write(path, data: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """隔离三层路径：用户级指向临时目录，避免污染真实 ~/.newcode。"""
    monkeypatch.setattr(L, "HOOK_FILE_USER", str(tmp_path / "user.yaml"))
    return tmp_path


class TestTierMerge:
    def test_three_tiers_priority_order(self, isolated, capsys):
        """本地 > 项目 > 用户 追加合并，优先级高者在前（F6.2）。"""
        tmp = isolated
        user = tmp / "user.yaml"
        _write(
            user,
            "hooks:\n  - {name: u1, event: turn_start, action: {type: prompt, text: user}}\n",
        )
        _write(
            tmp / ".newcode" / "config.yaml",
            "hooks:\n  - {name: p1, event: turn_start, action: {type: prompt, text: proj}}\n",
        )
        _write(
            tmp / ".newcode" / "config.local.yaml",
            "hooks:\n  - {name: l1, event: turn_start, action: {type: prompt, text: local}}\n",
        )
        eng = L.load(str(tmp))
        names = [r.name for r in eng.rules]
        assert names == ["l1", "p1", "u1"]  # 本地在前
        assert eng.sources == [
            str(tmp / ".newcode" / "config.local.yaml"),
            str(tmp / ".newcode" / "config.yaml"),
            str(user),
        ]

    def test_missing_files_skipped(self, isolated):
        """三处文件缺失不报错（F6.2）。"""
        eng = L.load(str(isolated))
        assert eng.rules == [] and eng.sources == []

    def test_invalid_yaml_warns_and_degrades(self, isolated, capsys):
        """整体 YAML 非法 → stderr 告警 + 该文件空，不阻断其它层（N2）。"""
        tmp = isolated
        _write(
            tmp / ".newcode" / "config.local.yaml",
            "hooks:\n  - {name: ok, event: turn_start, action: {type: prompt, text: x}}\n",
        )
        _write(tmp / ".newcode" / "config.yaml", ": : bad :\n\t")
        eng = L.load(str(tmp))
        assert len(eng.rules) == 1
        assert "格式错误" in capsys.readouterr().err

    def test_non_dict_top_level_warns(self, isolated, capsys):
        tmp = isolated
        _write(tmp / ".newcode" / "config.yaml", "just a string")
        eng = L.load(str(tmp))
        assert eng.rules == []
        assert "顶层必须是对象" in capsys.readouterr().err

    def test_same_name_conflict_keeps_high_priority(self, isolated, capsys):
        """同名冲突 → stderr 提示并保留高优先级层（F6.4/AC13）。"""
        tmp = isolated
        _write(
            tmp / ".newcode" / "config.local.yaml",
            "hooks:\n  - {name: dup, event: turn_start, action: {type: prompt, text: local}}\n",
        )
        _write(
            tmp / ".newcode" / "config.yaml",
            "hooks:\n  - {name: dup, event: turn_start, action: {type: prompt, text: proj}}\n",
        )
        eng = L.load(str(tmp))
        assert len(eng.rules) == 1
        assert eng.rules[0].source.endswith("config.local.yaml")
        assert "name 与已加载 hook 冲突" in capsys.readouterr().err


class TestHookValidation:
    """逐条校验失败 → stderr 定位（文件+条目+字段）并跳过，其余正常加载（F6.5/F6.6）。"""

    def _load_one(self, isolated, hook_yaml: str):
        tmp = isolated
        _write(tmp / ".newcode" / "config.yaml", "hooks:\n" + hook_yaml)
        return L.load(str(tmp))

    def test_unknown_event(self, isolated, capsys):
        eng = self._load_one(
            isolated,
            "  - {name: x, event: UnknownEvent, action: {type: prompt, text: t}}\n  - {name: ok, event: turn_start, action: {type: prompt, text: t}}\n",
        )
        assert [r.name for r in eng.rules] == ["ok"]
        assert 'unknown event "UnknownEvent"' in capsys.readouterr().err

    def test_unknown_action_type(self, isolated, capsys):
        eng = self._load_one(
            isolated,
            "  - {name: x, event: turn_start, action: {type: magic}}\n  - {name: ok, event: turn_start, action: {type: prompt, text: t}}\n",
        )
        assert [r.name for r in eng.rules] == ["ok"]
        assert 'unknown action type "magic"' in capsys.readouterr().err

    def test_missing_required_field(self, isolated, capsys):
        # command 缺 command 字段
        eng = self._load_one(
            isolated,
            "  - {name: x, event: turn_start, action: {type: command}}\n  - {name: ok, event: turn_start, action: {type: prompt, text: t}}\n",
        )
        assert [r.name for r in eng.rules] == ["ok"]
        assert "command 动作缺少必填字段 command" in capsys.readouterr().err
        # http 缺 url
        eng = self._load_one(
            isolated, "  - {name: x, event: turn_start, action: {type: http}}\n"
        )
        assert eng.rules == []
        assert "http 动作缺少必填字段 url" in capsys.readouterr().err
        # agent 缺 agent_name
        eng = self._load_one(
            isolated,
            "  - {name: x, event: turn_start, action: {type: agent, prompt: t}}\n",
        )
        assert eng.rules == []
        assert "agent 动作缺少必填字段 agent_name" in capsys.readouterr().err

    def test_all_of_and_any_of_together(self, isolated, capsys):
        """if 顶层同时出现 all_of 与 any_of → 报错跳过（F4.1/AC17）。"""
        eng = self._load_one(
            isolated,
            "  - {name: x, event: turn_start, action: {type: prompt, text: t}, if: {all_of: [], any_of: []}}\n  - {name: ok, event: turn_start, action: {type: prompt, text: t}}\n",
        )
        assert [r.name for r in eng.rules] == ["ok"]
        assert "all_of / any_of 之一" in capsys.readouterr().err

    def test_regex_compile_failure(self, isolated, capsys):
        eng = self._load_one(
            isolated,
            '  - name: x\n    event: turn_start\n    action: {type: prompt, text: t}\n    if:\n      all_of:\n        - field: prompt\n          match: {type: regex, value: "[invalid"}\n  - {name: ok, event: turn_start, action: {type: prompt, text: t}}\n',
        )
        assert [r.name for r in eng.rules] == ["ok"]
        assert "match 非法" in capsys.readouterr().err

    def test_async_on_blocking_event(self, isolated, capsys):
        """async + pre_tool_use → 报错跳过（F2.2/AC10）。"""
        eng = self._load_one(
            isolated,
            "  - {name: x, event: pre_tool_use, async: true, action: {type: prompt, text: t}}\n  - {name: ok, event: turn_start, action: {type: prompt, text: t}}\n",
        )
        assert [r.name for r in eng.rules] == ["ok"]
        assert "async not allowed for blocking events" in capsys.readouterr().err

    def test_timeout_invalid(self, isolated, capsys):
        eng = self._load_one(
            isolated,
            '  - {name: x, event: turn_start, action: {type: prompt, text: t}, timeout: "abc"}\n',
        )
        assert eng.rules == []
        assert "timeout 格式非法" in capsys.readouterr().err

    def test_missing_name(self, isolated, capsys):
        eng = self._load_one(
            isolated, "  - {event: turn_start, action: {type: prompt, text: t}}\n"
        )
        assert eng.rules == []
        assert "name 必填" in capsys.readouterr().err


class TestParseDuration:
    def test_units(self):
        assert L._parse_duration("30s") == 30.0
        assert L._parse_duration("5m") == 300.0
        assert L._parse_duration("2h") == 7200.0
        assert L._parse_duration("1.5") == 1.5

    def test_number_and_default(self):
        assert L._parse_duration(30) == 30.0
        assert L._parse_duration(None) == 30.0

    def test_invalid(self):
        assert L._parse_duration("abc") is None
        assert L._parse_duration([]) is None
