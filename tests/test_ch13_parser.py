"""ch13 subagent/parser.py 定义解析测试。

防的 bug：
- permissionMode: dontAsk 被当未知模式拒绝（应解析为 dont_ask=True + mode=default）
- 未知 model/mode 不降级直接抛错（F2.4 要求 warning 降级缺省）
- 缺 description 不报错 → 角色没说明被主 Agent 误用
- name 取文件基名时归一化失败（-/_ 混入导致 subagent_type 不可用）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mewcode.subagent.parser import parse_definition
from mewcode.subagent.types import DefinitionParseError, Source


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_full_frontmatter(tmp_path):
    p = _write(
        tmp_path,
        "ok.md",
        "---\nname: my-role\ndescription: 测试\nmodel: haiku\n"
        "permissionMode: acceptEdits\nmaxTurns: 5\nbackground: true\n"
        "disallowedTools:\n  - execute_command\n---\n你是测试角色\n",
    )
    d = parse_definition(str(p), Source.PROJECT)
    assert d.name == "my-role"
    assert d.model == "haiku"
    assert d.permission_mode.value == "acceptEdits"
    assert d.max_turns == 5 and d.background is True
    assert d.disallowed_tools == ["execute_command"]
    assert d.body.startswith("你是测试角色")
    assert d.source is Source.PROJECT


def test_dont_ask_maps_to_flag(tmp_path):
    p = _write(
        tmp_path,
        "da.md",
        "---\nname: da\ndescription: x\npermissionMode: dontAsk\n---\nb",
    )
    d = parse_definition(str(p), Source.USER)
    assert d.dont_ask is True and d.permission_mode.value == "default"


def test_unknown_model_falls_back(capsys, tmp_path):
    p = _write(tmp_path, "m.md", "---\nname: m\ndescription: x\nmodel: bogus\n---\nb")
    d = parse_definition(str(p), Source.USER)
    assert d.model == "inherit"
    assert "falling back to inherit" in capsys.readouterr().err


def test_unknown_mode_falls_back(capsys, tmp_path):
    p = _write(
        tmp_path,
        "md.md",
        "---\nname: md\ndescription: x\npermissionMode: weird\n---\nb",
    )
    d = parse_definition(str(p), Source.USER)
    assert d.permission_mode.value == "default"
    assert "falling back to default" in capsys.readouterr().err


def test_missing_description_raises(tmp_path):
    p = _write(tmp_path, "nd.md", "---\nname: nd\n---\nb")
    with pytest.raises(DefinitionParseError, match="description"):
        parse_definition(str(p), Source.USER)


def test_name_from_filename(tmp_path):
    p = _write(tmp_path, "base-name.md", "---\ndescription: x\n---\nb")
    d = parse_definition(str(p), Source.USER)
    assert d.name == "base-name"


def test_unclosed_frontmatter_raises(tmp_path):
    p = _write(tmp_path, "bad.md", "---\nname: bad\ndescription: x\nb")
    with pytest.raises(DefinitionParseError):
        parse_definition(str(p), Source.USER)
