"""Skill 解析器单测（T23）：frontmatter 分离校验、名字归一化、目录型 tool.json。

防的 bug：frontmatter 缺失/未闭合/非 YAML/非 dict/必填缺失未抛 SkillParseError
（会静默生成残缺 Skill）；名字未归一化导致 /名字 注册失败；mode/context 非法值
直接抛错阻断加载（F1.2 要求 warning 降级）；目录型 tool.json 未读入。
"""

from pathlib import Path

import pytest

from newcode.skills.parser import (
    normalize_name,
    parse_frontmatter_and_body,
    parse_skill,
)
from newcode.skills.types import SkillParseError, SkillSource

VALID_FM = "---\nname: my-skill\ndescription: A test skill\n---\nbody here"


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ── normalize_name（F1.4）──────────────────────────────────


def test_normalize_name_lowercases_and_dashes():
    """防 bug：大写/空格/符号未归一化会注册成非法 /名字 命令。"""
    assert normalize_name("My_Skill") == "my-skill"
    assert normalize_name("  Hello WORLD!! ") == "hello-world"
    assert normalize_name("commit") == "commit"


# ── parse_frontmatter_and_body ─────────────────────────────


def test_parse_frontmatter_and_body_valid():
    fm, body = parse_frontmatter_and_body(VALID_FM)
    assert fm["name"] == "my-skill"
    assert body == "body here"


def test_parse_missing_opening_frontmatter_raises():
    """防 bug：无首行 --- 的文件若被当 Skill 解析，正文会误入 frontmatter。"""
    with pytest.raises(SkillParseError):
        parse_frontmatter_and_body("name: x\n---\nbody")


def test_parse_unclosed_frontmatter_raises():
    with pytest.raises(SkillParseError):
        parse_frontmatter_and_body("---\nname: x\nbody")


def test_parse_invalid_yaml_raises():
    with pytest.raises(SkillParseError):
        parse_frontmatter_and_body("---\nname: [unclosed\n---\nbody")


def test_parse_non_dict_frontmatter_raises():
    with pytest.raises(SkillParseError):
        parse_frontmatter_and_body("---\n- a\n- b\n---\nbody")


# ── parse_skill（单文件布局）───────────────────────────────


def test_parse_single_file_skill(tmp_path):
    p = _write(tmp_path, "good.md", VALID_FM)
    skill = parse_skill(p, SkillSource.BUILTIN)
    assert skill.name == "my-skill"
    assert skill.meta.description == "A test skill"
    assert skill.prompt_body == "body here"
    assert skill.source == SkillSource.BUILTIN
    assert not skill.is_directory


def test_parse_missing_name_raises(tmp_path):
    p = _write(
        tmp_path,
        "x.md",
        "---\ndescription: no name\n---\nbody",
    )
    with pytest.raises(SkillParseError):
        parse_skill(p, SkillSource.USER)


def test_parse_missing_description_raises(tmp_path):
    p = _write(tmp_path, "x.md", "---\nname: x\n---\nbody")
    with pytest.raises(SkillParseError):
        parse_skill(p, SkillSource.USER)


def test_parse_invalid_name_format_raises(tmp_path):
    """防 bug：归一化后名字非法（如以数字开头）必须抛错跳过，不注册坏命令。"""
    p = _write(tmp_path, "x.md", "---\nname: 9bad name!\ndescription: d\n---\nbody")
    with pytest.raises(SkillParseError):
        parse_skill(p, SkillSource.USER)


def test_parse_nonexistent_file_raises(tmp_path):
    with pytest.raises(SkillParseError):
        parse_skill(tmp_path / "missing.md", SkillSource.USER)


# ── mode / context 非法值降级（F1.2 补充说明）──────────────


def test_invalid_mode_falls_back_to_inline(tmp_path, caplog):
    p = _write(
        tmp_path,
        "x.md",
        "---\nname: x\ndescription: d\nmode: weird\n---\nbody",
    )
    skill = parse_skill(p, SkillSource.USER)
    assert skill.meta.mode == "inline"
    assert "invalid mode" in caplog.text


def test_invalid_context_falls_back_to_none(tmp_path):
    p = _write(
        tmp_path,
        "x.md",
        "---\nname: x\ndescription: d\nmode: fork\ncontext: bogus\n---\nbody",
    )
    skill = parse_skill(p, SkillSource.USER)
    assert skill.meta.mode == "fork"
    assert skill.meta.fork_context == "none"


def test_fork_mode_with_context(tmp_path):
    p = _write(
        tmp_path,
        "x.md",
        "---\nname: x\ndescription: d\nmode: fork\ncontext: recent\n---\nbody",
    )
    skill = parse_skill(p, SkillSource.USER)
    assert skill.meta.mode == "fork"
    assert skill.meta.fork_context == "recent"


def test_allowed_tools_parsed(tmp_path):
    p = _write(
        tmp_path,
        "x.md",
        "---\nname: x\ndescription: d\nallowedTools:\n  - read_file\n  - search_code\n---\nbody",
    )
    skill = parse_skill(p, SkillSource.USER)
    assert skill.meta.allowed_tools == ["read_file", "search_code"]


# ── 目录型布局（F1.1/F9.2）────────────────────────────────


def test_directory_layout_skill(tmp_path):
    d = tmp_path / "myskill"
    d.mkdir()
    (d / "SKILL.md").write_text(VALID_FM, encoding="utf-8")
    (d / "references").mkdir()
    (d / "references" / "tool.py").write_text("print('hi')", encoding="utf-8")
    (d / "tool.json").write_text(
        '{"mytool": {"description": "do a thing", "parameters": {"type": "object"},'
        ' "entrypoint": "references/tool.py"}}',
        encoding="utf-8",
    )
    skill = parse_skill(d, SkillSource.PROJECT)
    assert skill.is_directory
    assert skill.name == "my-skill"  # 注册名取 frontmatter name（归一化）
    assert len(skill.tools) == 1
    assert skill.tools[0].name == "mytool"
    assert skill.tools[0].entrypoint == "references/tool.py"


def test_directory_layout_invalid_tool_json_raises(tmp_path):
    d = tmp_path / "bad"
    d.mkdir()
    (d / "SKILL.md").write_text(VALID_FM, encoding="utf-8")
    (d / "tool.json").write_text(
        '{"tool": {"name": "t", "description": "d"}}', encoding="utf-8"
    )
    with pytest.raises(SkillParseError):  # 缺 entrypoint（F3.8）
        parse_skill(d, SkillSource.PROJECT)


def test_single_file_skill_ignores_tool_json(tmp_path):
    """防 bug：单文件布局的同目录 tool.json 不应被误读（只目录型读）。"""
    p = _write(tmp_path, "x.md", VALID_FM)
    (tmp_path / "tool.json").write_text(
        '{"mytool": {"description": "d", "parameters": {}, "entrypoint": "references/a.py"}}',
        encoding="utf-8",
    )
    skill = parse_skill(p, SkillSource.USER)
    assert skill.tools == ()
