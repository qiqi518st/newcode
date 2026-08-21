"""/skill 管理命令 handler 单测（T25，ch11 变更后）：list/info/reload/load/unload。

用 RecordingUI 驱动真实 handler 路径（CLAUDE.md：接线测试必须自动跑，不依赖真实终端）。

ch11 变更：on/off 已合并进 load/unload（方向 A，持久化）：
- load = 启用（持久）+ 激活 + 恢复 /名字 命令
- unload = 禁用（持久）+ 失活 + 删 /名字 命令 + 清缓存；/skill list 仍可见（[disabled]）

防的 bug：list 排版不齐或漏来源层级/禁用状态（F7.1）；unload 后 list 完全消失
（用户无法发现被禁用的 Skill）；unload 未持久 disabled（F7.8/N12）；load 未恢复
命令注册；无 name 的 info/load/unload 未提示用法。
"""

import asyncio
from pathlib import Path

from mewcode.skills import ActiveSkills, Catalog, Executor
from mewcode.slash import CommandContext, CommandRegistry, RecordingUI
from mewcode.slash.commands.skill import handle_skill
from mewcode.slash.commands.skill_register import register_skills_as_commands
from mewcode.tools import Registry

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
    registry = CommandRegistry()
    executor = Executor(catalog, store, Registry(), None)
    # 模拟启动装配：注册全部 Skill 为 /名字 命令
    register_skills_as_commands(registry, catalog, executor)
    ctx = CommandContext(
        registry=registry,
        ui=ui,
        agent=None,
        conversation=None,
        plan_manager=None,
        catalog=catalog,
        active_skills=store,
        executor=executor,
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


def test_list_shows_disabled_state_after_unload(tmp_path):
    """防 bug：unload 后 /skill list 仍显示该 Skill 并标注 [disabled]（不能消失无痕迹）。"""
    ctx, ui, _, _ = _ctx(tmp_path)
    _run(ctx, "unload demo")
    _run(ctx, "list")
    text = "\n".join(ui.messages)
    assert "  demo" in text  # 仍可见
    assert "[disabled]" in text  # 标注禁用状态
    assert "other" in text  # 未禁用的正常显示


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
    assert "disabled: False" in text


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


# ── load（F7.4，ch11 变更：load = 原 load + on）─────────────


def test_load_activates_and_enables(tmp_path):
    """防 bug：load 激活 SOP + 从 disabled 移除（持久启用）。"""
    ctx, ui, catalog, store = _ctx(tmp_path)
    catalog.set_disabled("demo", True)  # 先禁用
    _run(ctx, "load demo")
    assert not catalog.is_disabled("demo")  # 启用（持久）
    assert store.names() == ["demo"]  # 激活
    assert "DEMO BODY" in store.snapshot()[0].body
    assert "已加载并启用 Skill: demo" in ui.messages[-1]


def test_load_restores_skill_command(tmp_path):
    """防 bug：unload 删掉 /名字 命令后，load 恢复注册（F2.4 同步）。"""
    ctx, _, _, _ = _ctx(tmp_path)
    _run(ctx, "unload demo")  # 删命令
    assert ctx.registry.get("demo") is None
    _run(ctx, "load demo")  # 恢复命令
    cmd = ctx.registry.get("demo")
    assert cmd is not None
    assert cmd.description.endswith("[skill]")


# ── unload（F7.7，ch11 变更：unload = 原 unload + off）──────


def test_unload_disables_and_deactivates(tmp_path):
    """防 bug：unload 禁用（持久）+ 立即失活 + 删 /名字 命令。"""
    ctx, ui, catalog, store = _ctx(tmp_path)
    store.activate("demo", "x")
    _run(ctx, "unload demo")
    assert catalog.is_disabled("demo")  # 持久禁用
    assert "demo" not in catalog.names()  # 从可用列表移除
    assert store.names() == []  # 立即失活
    assert ctx.registry.get("demo") is None  # 删 /名字 命令
    assert "demo" in [s.name for s in catalog.list_all()]  # 但 list_all 仍可见
    assert "已卸载并禁用 Skill: demo" in ui.messages[-1]


def test_unload_persists_across_restart(tmp_path):
    """防 bug：unload 落盘 disabled.json，重建 Catalog 后禁用保持（F7.8/N12）。"""
    ctx, _, _, _ = _ctx(tmp_path)
    _run(ctx, "unload demo")
    # 模拟重启：重新加载
    user = Path(tmp_path) / "user"
    c2 = Catalog.load(
        project_dir=Path(tmp_path) / "proj",
        user_skills_dir=user,
        builtin_dir=Path(tmp_path) / "builtin",
    )
    assert c2.is_disabled("demo")
    assert "demo" not in c2.names()
    # load 恢复
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
    _run(ctx2, "load demo")
    assert not c2.is_disabled("demo")
    assert "demo" in c2.names()


# ── 退化输入 ───────────────────────────────────────────────


def test_unknown_subcommand_shows_usage(tmp_path):
    ctx, ui, _, _ = _ctx(tmp_path)
    _run(ctx, "frobnicate")
    assert "/skill <list|info|reload|load|unload>" in ui.messages[-1]
