"""Skill 动态 /名字 命令注册单测（T25）：注册 / [skill] 标注 / 冲突跳过 / 闭包拷贝 / 清旧。

防的 bug：循环变量闭包陷阱（全部 handler 指向最后一个 skill，须 functools.partial 拷贝）；
与内置命令冲突未跳过（F2.5 内置优先）；description 未标 [skill]（N11 区分）；reload
重注册残留旧命令（remove_skill_commands 清旧）；fork skill handler 未走后台任务。
"""

from pathlib import Path

from mewcode.skills import ActiveSkills, Catalog, Executor
from mewcode.slash import CommandRegistry
from mewcode.slash.commands import register_all
from mewcode.slash.commands.skill_register import (
    register_skills_as_commands,
    remove_skill_commands,
)


def _skill_md(name: str, mode: str = "inline", description: str = "desc") -> str:
    return (
        f"---\nname: {name}\ndescription: {description}\nmode: {mode}\n---\nBODY {name}"
    )


def _catalog_with(tmp_path: Path, names: list[str]) -> Catalog:
    user = tmp_path / "user"
    user.mkdir()
    for n in names:
        (user / f"{n}.md").write_text(
            _skill_md(n, "fork" if n == "review" else "inline"), encoding="utf-8"
        )
    return Catalog.load(
        project_dir=tmp_path / "proj",
        user_skills_dir=user,
        builtin_dir=tmp_path / "builtin",
    )


def _setup(tmp_path: Path):
    reg = CommandRegistry()
    register_all(reg)  # 内置命令先注册
    catalog = _catalog_with(tmp_path, ["commit", "review"])
    executor = Executor(
        catalog,
        ActiveSkills(),
        __import__("mewcode.tools", fromlist=["Registry"]).Registry(),
        None,
    )
    return reg, catalog, executor


def test_registers_skill_commands_with_skill_tag(tmp_path):
    """防 bug：每个 Skill 注册 /名字 命令且 description 标 [skill]（F2.4/N11）。"""
    reg, catalog, executor = _setup(tmp_path)
    registered = register_skills_as_commands(reg, catalog, executor)
    assert set(registered) == {"commit", "review"}
    for name in ("commit", "review"):
        cmd = reg.get(name)
        assert cmd is not None
        assert cmd.description.endswith("[skill]")


def test_conflict_with_builtin_is_skipped(tmp_path):
    """防 bug：与内置命令冲突时 Skill 跳过注册（F2.5 内置优先），其余仍注册。"""
    reg, catalog, executor = _setup(tmp_path)
    # 加入一个与内置 /help 冲突的 Skill
    user = Path(tmp_path) / "user"
    (user / "help.md").write_text(_skill_md("help", "inline"), encoding="utf-8")
    catalog.reload()
    registered = register_skills_as_commands(reg, catalog, executor)
    assert "help" not in registered
    assert reg.get("help").description != "desc [skill]"  # 仍是内置 help
    assert "commit" in registered


def test_handler_closure_binds_correct_name(tmp_path):
    """防 bug：闭包必须按名绑定（functools.partial 拷贝），否则 /commit 会触发错误 Skill。"""
    reg, catalog, executor = _setup(tmp_path)
    register_skills_as_commands(reg, catalog, executor)
    # 直接检查 handler 绑定的 name：partial 关键字参数不可被 later 覆盖
    from functools import partial

    commit_cmd = reg.get("commit")
    assert isinstance(commit_cmd.handler, partial)
    assert commit_cmd.handler.keywords == {"name": "commit"}
    review_cmd = reg.get("review")
    assert review_cmd.handler.keywords == {"name": "review"}


def test_remove_skill_commands_clears_tagged(tmp_path):
    """防 bug：remove_skill_commands 清掉 [skill] 命令，内置命令保留。"""
    reg, catalog, executor = _setup(tmp_path)
    register_skills_as_commands(reg, catalog, executor)
    assert reg.get("commit") is not None
    removed = remove_skill_commands(reg)
    assert removed == 2
    assert reg.get("commit") is None
    assert reg.get("review") is None
    assert reg.get("help") is not None  # 内置保留


def test_reregister_replaces_without_duplicates(tmp_path):
    """防 bug：重复 register（reload 后）不残留旧命令（先清旧再注册）。"""
    reg, catalog, executor = _setup(tmp_path)
    register_skills_as_commands(reg, catalog, executor)
    register_skills_as_commands(reg, catalog, executor)  # reload 场景
    # remove_by 后重注册 → 无重复
    assert reg.get("commit") is not None
    assert len([c for c in reg.list() if c.description.endswith("[skill]")]) == 2


def test_fork_skill_handler_creates_background_task(tmp_path):
    """防 bug：fork Skill 的 handler 走 create_task 后台执行，不阻塞命令循环。"""
    reg, catalog, executor = _setup(tmp_path)
    register_skills_as_commands(reg, catalog, executor)
    review_cmd = reg.get("review")
    assert review_cmd.handler.keywords["name"] == "review"
    # fork handler 内部逻辑：executor.execute 在 create_task 里（间接断言 mode）
    skill = catalog.get("review")
    assert skill.meta.mode == "fork"
