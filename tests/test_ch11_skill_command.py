"""/skill 管理命令 handler 单测（T25）：list/info/reload/load/on/off/unload。

用 RecordingUI 驱动真实 handler 路径（CLAUDE.md：接线测试必须自动跑，不依赖真实终端）。

防的 bug：list 排版不齐或漏来源层级（F7.1）；info 漏字段（F7.2）；off 后未从
可用列表移除 / 未持久 disabled（F7.6/F7.8）；unload 未清 disabled 标记（F7.7）；
无 name 的 info/load/on/off/unload 未提示用法。
"""

import asyncio
from pathlib import Path

from mewcode.skills import ActiveSkills, Catalog
from mewcode.slash import CommandContext, CommandRegistry, RecordingUI
from mewcode.slash.commands.skill import handle_skill

USER_SKILL = "---\nname: demo\ndescription: A demo skill\n---\nDEMO BODY"


def _catalog(tmp_path: Path) -> Catalog:
    user = tmp_path / "user"
    user.mkdir()
    (user / "demo.md").write_text(USER_SKILL, encoding="utf-8")
    (user / "other.md").write_text(
        "---\nname: other\ndescription: Another skill\n---\nOTHER BODY",
        encoding="utf-8",
    )
    return Catalog.load(
        project_dir=tmp_path / "proj",
        user_skills_dir=user,
        builtin_dir=tmp_path / "builtin",
    )


def _ctx(tmp_path: Path) -> tuple[CommandContext, RecordingUI, Catalog, ActiveSkills]:
    ui = RecordingUI()
    catalog = _catalog(tmp_path)
    store = ActiveSkills()
    ctx = CommandContext(
        registry=CommandRegistry(),
        ui=ui,
        agent=None,
        conversation=None,
        plan_manager=None,
        catalog=catalog,
        active_skills=store,
        executor=None,
    )
    return ctx, ui, catalog, store


def _run(ctx, args: str) -> None:
    asyncio.run(handle_skill(ctx, args))


# ── list（F7.1）────────────────────────────────────────────


def test_list_shows_alignment_and_source(tmp_path):
    ctx, ui, _, _ = _ctx(tmp_path)
    _run(ctx, "list")
    text = "\n".join(ui.messages)
    assert "  demo" in text
    assert "A demo skill" in text
    assert "[user]" in text


def test_list_shows_active_marker(tmp_path):
    ctx, ui, _, store = _ctx(tmp_path)
    store.activate("demo", "x")
    _run(ctx, "list")
    assert "已激活: demo" in ui.messages[-1]


# ── info（F7.2）────────────────────────────────────────────


def test_info_shows_all_fields(tmp_path):
    ctx, ui, _, _ = _ctx(tmp_path)
    _run(ctx, "info demo")
    text = "\n".join(ui.messages)
    assert "name: demo" in text
    assert "description: A demo skill" in text
    assert "mode: inline" in text
    assert "source: user" in text
    assert "active: False" in text


def test_info_unknown(tmp_path):
    ctx, ui, _, _ = _ctx(tmp_path)
    _run(ctx, "info nope")
    assert "未知 Skill" in ui.messages[-1]


def test_info_without_name_hints_usage(tmp_path):
    ctx, ui, _, _ = _ctx(tmp_path)
    _run(ctx, "info")
    assert "用法: /skill info" in ui.messages[-1]


# ── reload（F7.3）──────────────────────────────────────────


def test_reload_single_skill(tmp_path):
    ctx, ui, _, _ = _ctx(tmp_path)
    _run(ctx, "reload demo")
    assert "已重载 Skill: demo" in ui.messages[-1]


def test_reload_full_rescans(tmp_path):
    ctx, ui, catalog, _ = _ctx(tmp_path)
    # 新增一个 Skill 后全量重扫
    (Path(tmp_path) / "user" / "newone.md").write_text(
        "---\nname: newone\ndescription: added later\n---\nNEW", encoding="utf-8"
    )
    _run(ctx, "reload")
    assert "newone" in catalog.names()
    assert "新增: newone" in ui.messages[-1]


# ── load（F7.4）────────────────────────────────────────────


def test_load_activates_skill(tmp_path):
    ctx, ui, _, store = _ctx(tmp_path)
    _run(ctx, "load demo")
    assert store.names() == ["demo"]
    assert "DEMO BODY" in store.snapshot()[0].body
    assert "已加载 Skill: demo" in ui.messages[-1]


# ── on / off（F7.5/F7.6/F7.8）──────────────────────────────


def test_off_disables_and_deactivates(tmp_path):
    ctx, _, catalog, store = _ctx(tmp_path)
    store.activate("demo", "x")
    _run(ctx, "off demo")
    assert catalog.is_disabled("demo")
    assert "demo" not in catalog.names()
    assert store.names() == []  # 已激活立即失活（F7.6）


def test_off_persists_across_restart(tmp_path):
    """防 bug：/skill off 落盘 disabled.json，重建 Catalog 后禁用保持（F7.8/N12）。"""
    ctx, _, _, _ = _ctx(tmp_path)
    _run(ctx, "off demo")
    # 模拟重启：重新加载
    user = Path(tmp_path) / "user"
    c2 = Catalog.load(
        project_dir=Path(tmp_path) / "proj",
        user_skills_dir=user,
        builtin_dir=Path(tmp_path) / "builtin",
    )
    assert c2.is_disabled("demo")
    assert "demo" not in c2.names()
    # on 恢复
    ctx2 = CommandContext(
        registry=CommandRegistry(),
        ui=RecordingUI(),
        agent=None,
        conversation=None,
        plan_manager=None,
        catalog=c2,
        active_skills=ActiveSkills(),
        executor=None,
    )
    _run(ctx2, "on demo")
    assert not c2.is_disabled("demo")
    assert "demo" in c2.names()


# ── unload（F7.7）──────────────────────────────────────────


def test_unload_removes_and_clears_disabled(tmp_path):
    ctx, ui, catalog, store = _ctx(tmp_path)
    catalog.set_disabled("demo", True)
    _run(ctx, "unload demo")
    assert "demo" not in catalog.names()
    assert not catalog.is_disabled("demo")
    assert store.names() == []
    assert "已卸载 Skill: demo" in ui.messages[-1]


# ── 退化输入 ───────────────────────────────────────────────


def test_unknown_subcommand_shows_usage(tmp_path):
    ctx, ui, _, _ = _ctx(tmp_path)
    _run(ctx, "frobnicate")
    assert "/skill <list|info|reload|load|on|off|unload>" in ui.messages[-1]
