"""ch13 subagent/types.py 数据结构测试。

防的 bug：
- AgentDefinition 缺省值漂移（model 非 inherit、max_turns 非 10）会导致子 Agent 用错模型/轮数
- is_fork() 靠 name=="__fork__"，若改名会静默让 Fork 走上定义式路径（正文空白）
- NOTIFICATION_XML 模板字段名拼错会让完成通知渲染成字面量
"""

from __future__ import annotations

from mewcode.permission.modes import PermissionMode
from mewcode.subagent.errors import MaxTurnsReached
from mewcode.subagent.types import (
    DEFAULT_MAX_TURNS,
    NOTIFICATION_XML,
    RESULT_TRUNCATE_CHARS,
    AgentDefinition,
    DefinitionParseError,
    Source,
)


def test_agent_definition_defaults():
    d = AgentDefinition(name="x", description="d", source=Source.BUILTIN)
    assert d.model == "inherit"
    assert d.max_turns == DEFAULT_MAX_TURNS == 10
    assert d.permission_mode is PermissionMode.DEFAULT
    assert d.dont_ask is False and d.background is False and d.enabled is True
    assert d.tools == [] and d.disallowed_tools == [] and d.body == ""


def test_is_fork_by_name():
    assert AgentDefinition(name="x", description="d").is_fork() is False
    assert AgentDefinition(name="__fork__", description="d").is_fork() is True


def test_source_ordering_and_str():
    # 加载顺序用 IntEnum 表达优先级（高数值 = 高优先级先写）
    assert Source.BUILTIN < Source.USER < Source.PROJECT < Source.PLUGIN
    assert str(Source.BUILTIN) == "builtin"
    assert str(Source.USER) == "user"
    assert str(Source.PROJECT) == "project"
    assert str(Source.PLUGIN) == "plugin"


def test_max_turns_reached_carries_result():
    e = MaxTurnsReached("partial", None, 3)
    assert e.text == "partial" and e.tool_count == 3
    assert "max turns" in str(e)


def test_definition_parse_error_message():
    e = DefinitionParseError("a.md", "bad")
    assert e.path == "a.md" and e.reason == "bad"
    assert "a.md" in str(e) and "bad" in str(e)


def test_notification_template_fields():
    xml = NOTIFICATION_XML.format(
        task_id="agent-1", status="completed", summary="Agent done", result="r"
    )
    assert "<task-notification>" in xml
    assert "<task-id>agent-1</task-id>" in xml
    assert "<status>completed</status>" in xml
    assert RESULT_TRUNCATE_CHARS == 800
