"""LoadSkill 系统工具单测（T24）：激活确认 / unknown / 目录型注册 / 嵌套 / 系统豁免。

防的 bug：load_skill 未标记 is_system/read_only（F3.5/N5 豁免失效）；
激活后不返回简短确认而返回完整 SOP（F4.2.3 占上下文）；unknown skill 返回 ok
（应为 error）；目录型 skill 激活后 tool.json 工具未注册进 registry。
"""

import asyncio
from pathlib import Path

from mewcode.skills import ActiveSkills, Catalog
from mewcode.tools.load_skill import LoadSkillTool
from mewcode.tools.registry import Registry


def _catalog_with(tmp_path: Path) -> Catalog:
    user = tmp_path / "user"
    user.mkdir()
    (user / "alpha.md").write_text(
        "---\nname: alpha\ndescription: alpha skill\n---\nALPHA SOP body",
        encoding="utf-8",
    )
    (user / "beta.md").write_text(
        "---\nname: beta\ndescription: beta skill\n---\nBETA SOP body", encoding="utf-8"
    )
    return Catalog.load(
        project_dir=tmp_path / "proj",
        user_skills_dir=user,
        builtin_dir=tmp_path / "builtin",
    )


def _make_components(tmp_path: Path):
    catalog = _catalog_with(tmp_path)
    store = ActiveSkills()
    registry = Registry()
    tool = LoadSkillTool(catalog, store, registry)
    return catalog, store, registry, tool


def test_is_system_and_read_only(tmp_path):
    """防 bug：load_skill 必须 is_system=True（豁免 allowedTools）与 read_only=True（不弹权限）。"""
    _, _, _, tool = _make_components(tmp_path)
    assert tool.name == "load_skill"
    assert tool.is_system is True
    assert tool.read_only is True


def test_activate_returns_short_confirmation(tmp_path):
    """防 bug：确认信息简短、不返回完整 SOP（F4.2.3 防 tool_result 占上下文）。"""
    _, store, _, tool = _make_components(tmp_path)
    res = asyncio.run(tool.execute({"name": "alpha"}))
    assert res.status == "ok"
    assert "alpha activated" in res.output
    assert "SOP pinned to environment context" in res.output
    assert "ALPHA SOP body" not in res.output  # 不返回完整 SOP
    assert store.names() == ["alpha"]
    assert "ALPHA SOP body" in store.snapshot()[0].body  # body 进了激活态


def test_unknown_skill_returns_error(tmp_path):
    _, store, _, tool = _make_components(tmp_path)
    res = asyncio.run(tool.execute({"name": "nope"}))
    assert res.status == "error"
    assert "unknown skill: nope" in res.error
    assert store.names() == []


def test_missing_name_argument_returns_error(tmp_path):
    _, _, _, tool = _make_components(tmp_path)
    res = asyncio.run(tool.execute({}))
    assert res.status == "error"
    assert "missing required argument" in res.error


def test_directory_skill_registers_tool(tmp_path):
    """防 bug：目录型 Skill 激活后 tool.json 工具注册进 registry（F4.2 第 2 步）。"""
    user = tmp_path / "user"
    d = user / "toolskill"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: toolskill\ndescription: has tool\n---\nTOOL SOP", encoding="utf-8"
    )
    (d / "references").mkdir()
    (d / "references" / "tool.py").write_text("print('hi')", encoding="utf-8")
    (d / "tool.json").write_text(
        '{"custom_tool": {"description": "custom", "parameters": {"type": "object"},'
        ' "entrypoint": "references/tool.py"}}',
        encoding="utf-8",
    )
    catalog = Catalog.load(
        project_dir=tmp_path / "proj",
        user_skills_dir=user,
        builtin_dir=tmp_path / "builtin",
    )
    store = ActiveSkills()
    registry = Registry()
    tool = LoadSkillTool(catalog, store, registry)
    res = asyncio.run(tool.execute({"name": "toolskill"}))
    assert res.status == "ok"
    assert registry.get("custom_tool") is not None


def test_nested_activation_second_skill(tmp_path):
    """防 bug：Skill A 激活后，SOP 里再调 load_skill 激活 B（嵌套触发，F3.5/F5.4）。"""
    _, store, _, tool = _make_components(tmp_path)
    # 模拟 A 的 SOP 调 load_skill({name: beta}) 嵌套激活 B
    asyncio.run(tool.execute({"name": "alpha"}))
    asyncio.run(tool.execute({"name": "beta"}))
    assert store.names() == ["alpha", "beta"]
    assert "BETA SOP body" in store.snapshot()[1].body
