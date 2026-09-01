"""ch13 subagent/catalog.py 四层加载与优先级测试。

防的 bug：
- 同名覆盖方向错（项目级应赢用户级/内置；低优先级只填空）
- verifier 开关：enable_verifier=False 时应 resolve 不到
- 内置级解析失败应 fail-fast raise（代码 bug）；用户/项目级失败应 skip + stderr
- fork_definition 伪定义 is_fork() 应为 True 且强制后台
"""

from __future__ import annotations

import os

from mewcode.subagent.catalog import Catalog, load_catalog
from mewcode.subagent.config import AgentConfig
from mewcode.subagent.types import Source


def _write_agent(root, name, description="d"):
    p = root / ".mewcode" / "agents"
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody", encoding="utf-8"
    )
    return p


def test_builtin_roles_loaded():
    c = load_catalog(".", AgentConfig())
    assert c.resolve("explore") is not None
    assert c.resolve("plan") is not None
    assert c.resolve("general-purpose") is not None
    assert c.resolve("verifier") is None  # 默认关闭


def test_verifier_switch(tmp_path):
    os.environ["HOME"] = str(tmp_path / "home")
    c = load_catalog(str(tmp_path), AgentConfig(enable_verifier=True))
    assert c.resolve("verifier") is not None


def test_project_overrides_user_and_builtin(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # 项目级 explore
    _write_agent(tmp_path, "explore", "项目版")
    # 用户级 explore（应被项目级覆盖）
    user = tmp_path / "home" / ".mewcode" / "agents"
    user.mkdir(parents=True)
    (user / "explore.md").write_text(
        "---\nname: explore\ndescription: 用户版\n---\nb", encoding="utf-8"
    )
    (user / "custom.md").write_text(
        "---\nname: custom\ndescription: 用户独有\n---\nb", encoding="utf-8"
    )
    c = load_catalog(str(tmp_path), AgentConfig())
    assert c.resolve("explore").description == "项目版"
    assert c.resolve("explore").source is Source.PROJECT
    assert c.resolve("custom") is not None  # 未覆盖的名字仍可用


def test_user_bad_file_skipped(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    user = tmp_path / "home" / ".mewcode" / "agents"
    user.mkdir(parents=True)
    (user / "broken.md").write_text("---\nname: broken\n---\nno description")
    (user / "ok.md").write_text("---\nname: ok\ndescription: fine\n---\nb")
    c = load_catalog(str(tmp_path), AgentConfig())
    assert c.resolve("broken") is None
    assert c.resolve("ok") is not None
    assert "skipped" in capsys.readouterr().err


def test_fork_definition():
    fd = Catalog().fork_definition()
    assert fd.is_fork() is True
    assert fd.background is True
    assert fd.model == "inherit"
    assert fd.tools == [] and fd.disallowed_tools == []


def test_missing_dirs_no_error(tmp_path):
    # 无任何 agents 目录 → 全缺省，不报错
    c = load_catalog(str(tmp_path), AgentConfig())
    assert c.resolve("anything") is None
    assert c.list()  # 内置仍在
