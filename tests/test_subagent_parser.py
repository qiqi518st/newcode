"""ch14 subagent/parser.py isolation 字段解析测试（F8.1）。

防的 bug：
- isolation: worktree 未解析（子 Agent 启动时拿不到文件隔离声明）
- isolation: 非法值静默接受（应 stderr 警告并回落 ""）
"""

from __future__ import annotations

from mewcode.subagent.parser import parse_definition_text
from mewcode.subagent.types import Source


def _parse(frontmatter: str):
    raw = f"---\n{frontmatter}\n---\nbody"
    return parse_definition_text(raw, Source.BUILTIN, "test.md")


def test_isolation_default_empty():
    d = _parse("name: x\ndescription: d\n")
    assert d.isolation == ""


def test_isolation_worktree():
    d = _parse("name: x\ndescription: d\nisolation: worktree\n")
    assert d.isolation == "worktree"


def test_isolation_case_insensitive():
    d = _parse("name: x\ndescription: d\nisolation: Worktree\n")
    assert d.isolation == "worktree"


def test_isolation_invalid_falls_back(capsys):
    d = _parse("name: x\ndescription: d\nisolation: gibberish\n")
    assert d.isolation == ""
    assert "unknown isolation" in capsys.readouterr().err
