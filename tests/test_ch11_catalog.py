"""Skill 加载器单测（T23）：三级路径覆盖、失败隔离、热加载回退、validate_tools、disabled 持久。

防的 bug：同名 Skill 低优先级层覆盖高优先级（F2.1 要求项目 > 用户 > 内置）；
单个坏文件阻断整体加载（N3 失败隔离）；get() 重读失败不回退缓存（N7/F2.3）；
validate_tools 漏报引用不存在工具的 Skill（F2.7）；disabled 不落盘（F7.8）。
"""

from pathlib import Path

from mewcode.skills.catalog import Catalog


def _skill_md(name: str, description: str, body: str = "BODY") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}"


class _FakeReg:
    def __init__(self, names: list[str]) -> None:
        self._names = set(names)

    def get(self, name: str):
        return object() if name in self._names else None


def _make_roots(tmp_path: Path):
    """构造 项目级(.mewcode/skills) / 用户级(直接) / 内置级 三个根，返回 (proj, user, builtin)。"""
    proj = tmp_path / "proj"
    user = tmp_path / "user"
    builtin = tmp_path / "builtin"
    (proj / ".mewcode" / "skills").mkdir(parents=True)
    user.mkdir()
    builtin.mkdir()
    return proj, user, builtin


def _load(tmp_path: Path):
    proj, user, builtin = _make_roots(tmp_path)
    return Catalog.load(project_dir=proj, user_skills_dir=user, builtin_dir=builtin)


def test_three_level_load(tmp_path):
    c = _load(tmp_path)
    assert c.names() == []


def test_project_overrides_user_overrides_builtin(tmp_path):
    proj, user, builtin = _make_roots(tmp_path)
    (proj / ".mewcode" / "skills" / "dup.md").write_text(
        _skill_md("dup", "project level", "PROJECT BODY"), encoding="utf-8"
    )
    (user / "dup.md").write_text(
        _skill_md("dup", "user level", "USER BODY"), encoding="utf-8"
    )
    (builtin / "dup.md").write_text(
        _skill_md("dup", "builtin level", "BUILTIN BODY"), encoding="utf-8"
    )
    c = Catalog.load(project_dir=proj, user_skills_dir=user, builtin_dir=builtin)
    assert c.get_source_label("dup") == "project"
    assert "PROJECT BODY" in c.get("dup").prompt_body


def test_user_overrides_builtin_only(tmp_path):
    proj, user, builtin = _make_roots(tmp_path)
    (user / "x.md").write_text(_skill_md("x", "user"), encoding="utf-8")
    (builtin / "x.md").write_text(_skill_md("x", "builtin"), encoding="utf-8")
    c = Catalog.load(project_dir=proj, user_skills_dir=user, builtin_dir=builtin)
    assert c.get_source_label("x") == "user"


def test_bad_file_skipped_others_loaded(tmp_path):
    """防 bug：一个坏文件必须被跳过并 warning，其余 Skill 正常加载（N3 失败隔离）。"""
    proj, user, builtin = _make_roots(tmp_path)
    (user / "good.md").write_text(_skill_md("good", "ok"), encoding="utf-8")
    (user / "bad.md").write_text("not frontmatter at all\n---\n", encoding="utf-8")
    c = Catalog.load(project_dir=proj, user_skills_dir=user, builtin_dir=builtin)
    assert "good" in c.names()
    assert "bad" not in c.names()


def test_get_unknown_returns_none(tmp_path):
    c = _load(tmp_path)
    assert c.get("nonexistent") is None


def test_hot_reload_success(tmp_path):
    """防 bug：改源文件后 get() 返回新 body 无需重启（N7 热更新）。"""
    proj, user, builtin = _make_roots(tmp_path)
    path = user / "hot.md"
    path.write_text(_skill_md("hot", "v1", "OLD BODY"), encoding="utf-8")
    c = Catalog.load(project_dir=proj, user_skills_dir=user, builtin_dir=builtin)
    assert "OLD BODY" in c.get("hot").prompt_body
    path.write_text(_skill_md("hot", "v1", "NEW BODY"), encoding="utf-8")
    assert "NEW BODY" in c.get("hot").prompt_body


def test_hot_reload_failure_falls_back_to_cache(tmp_path, caplog):
    """防 bug：重读失败必须回退内存缓存旧版并记 warning（F2.3/N7），不能抛或返回 None。"""
    proj, user, builtin = _make_roots(tmp_path)
    path = user / "hot.md"
    path.write_text(_skill_md("hot", "v1", "CACHED BODY"), encoding="utf-8")
    c = Catalog.load(project_dir=proj, user_skills_dir=user, builtin_dir=builtin)
    # 第一次 get 成功，进缓存
    assert "CACHED BODY" in c.get("hot").prompt_body
    # 破坏源文件 → 重读失败 → 回退缓存
    path.write_text("garbage --- no frontmatter", encoding="utf-8")
    skill = c.get("hot")
    assert skill is not None
    assert "CACHED BODY" in skill.prompt_body
    assert "falling back to cached" in caplog.text


def test_validate_tools_reports_bad_allowedtools(tmp_path):
    """防 bug：allowedTools 引用不存在工具必须被 validate_tools 报出（F2.7/B 决策）。"""
    proj, user, builtin = _make_roots(tmp_path)
    (user / "bad.md").write_text(
        "---\nname: bad\ndescription: d\nallowedTools:\n  - nonexistent_tool\n---\nBODY",
        encoding="utf-8",
    )
    (user / "good.md").write_text(
        "---\nname: good\ndescription: d\nallowedTools:\n  - read_file\n---\nBODY",
        encoding="utf-8",
    )
    c = Catalog.load(project_dir=proj, user_skills_dir=user, builtin_dir=builtin)
    bad = c.validate_tools(_FakeReg(["read_file"]))
    assert "bad" in bad
    assert "good" not in bad


def test_directory_layout_detected(tmp_path):
    proj, user, builtin = _make_roots(tmp_path)
    d = user / "dirskill"
    d.mkdir()
    (d / "SKILL.md").write_text(_skill_md("dirskill", "dir"), encoding="utf-8")
    c = Catalog.load(project_dir=proj, user_skills_dir=user, builtin_dir=builtin)
    skill = c.get("dirskill")
    assert skill is not None and skill.is_directory


def test_disabled_persists_across_loads(tmp_path):
    """防 bug：disabled 集合落盘 disabled.json，重启后禁用状态保持（F7.8/N12）。"""
    proj, user, builtin = _make_roots(tmp_path)
    (user / "a.md").write_text(_skill_md("a", "one"), encoding="utf-8")
    (user / "b.md").write_text(_skill_md("b", "two"), encoding="utf-8")
    c = Catalog.load(project_dir=proj, user_skills_dir=user, builtin_dir=builtin)
    c.set_disabled("a", True)
    assert c.is_disabled("a")
    assert "a" not in c.names()
    assert "b" in c.names()
    # 重新加载（模拟重启）→ disabled 保持
    c2 = Catalog.load(project_dir=proj, user_skills_dir=user, builtin_dir=builtin)
    assert c2.is_disabled("a")
    assert "a" not in c2.names()
    c2.set_disabled("a", False)
    assert not c2.is_disabled("a")
    assert "a" in c2.names()


def test_reload_returns_added_removed(tmp_path):
    proj, user, builtin = _make_roots(tmp_path)
    (user / "a.md").write_text(_skill_md("a", "one"), encoding="utf-8")
    c = Catalog.load(project_dir=proj, user_skills_dir=user, builtin_dir=builtin)
    (user / "b.md").write_text(_skill_md("b", "two"), encoding="utf-8")
    (user / "a.md").unlink()
    added, removed = c.reload()
    assert added == ["b"]
    assert removed == ["a"]


def test_get_catalog_excludes_disabled(tmp_path):
    proj, user, builtin = _make_roots(tmp_path)
    (user / "a.md").write_text(_skill_md("a", "one"), encoding="utf-8")
    c = Catalog.load(project_dir=proj, user_skills_dir=user, builtin_dir=builtin)
    c.set_disabled("a", True)
    assert c.get_catalog() == []


def test_source_label_unknown(tmp_path):
    c = _load(tmp_path)
    assert c.get_source_label("missing") == ""
