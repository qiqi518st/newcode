"""CommandRegistry 单测（T2）：注册 / 冲突 / 查找 / 列举 / 前缀补全 / 防御拷贝。

防的 bug：冲突未检测（名字/别名撞车运行时静默失效，AC15/N4）；list 返回内部
dict 引用被外部改动污染；complete 未排除 hidden（AC14/F9.5）；大小写不敏感丢失。
"""

import pytest

from newcode.slash.registry import CommandDef, CommandRegistry


def _cmd(name: str, aliases: tuple[str, ...] = (), **kw) -> CommandDef:
    return CommandDef(name=name, handler=lambda ctx, args: None, aliases=aliases, **kw)


def test_register_ok():
    reg = CommandRegistry()
    reg.register(_cmd("help", description="帮助"))
    reg.register(_cmd("status", description="状态"))
    assert reg.get("help").description == "帮助"
    assert reg.get("HELP") is not None  # 大小写不敏感


def test_register_duplicate_name_raises():
    reg = CommandRegistry()
    reg.register(_cmd("help"))
    with pytest.raises(RuntimeError) as exc:
        reg.register(_cmd("help"))
    assert "help" in str(exc.value)


def test_register_duplicate_alias_raises():
    reg = CommandRegistry()
    reg.register(_cmd("exit", aliases=("quit",)))
    with pytest.raises(RuntimeError) as exc:
        reg.register(_cmd("quit"))
    assert "quit" in str(exc.value)
    # 别名与既有 name 撞
    reg2 = CommandRegistry()
    reg2.register(_cmd("exit", aliases=("quit",)))
    with pytest.raises(RuntimeError):
        reg2.register(_cmd("other", aliases=("exit",)))


def test_get_by_alias_case_insensitive():
    reg = CommandRegistry()
    reg.register(_cmd("exit", aliases=("quit", "q")))
    assert reg.get("Quit").name == "exit"
    assert reg.get("Q").name == "exit"
    assert reg.get("nonexistent") is None


def test_visible_sorted_excludes_hidden():
    reg = CommandRegistry()
    reg.register(_cmd("zebra"))
    reg.register(_cmd("alpha"))
    reg.register(_cmd("secret", hidden=True))
    assert [c.name for c in reg.list()] == ["alpha", "zebra"]
    assert [c.name for c in reg.list(include_hidden=True)] == [
        "alpha",
        "secret",
        "zebra",
    ]


def test_list_returns_copy():
    reg = CommandRegistry()
    reg.register(_cmd("help"))
    lst = reg.list()
    lst.clear()
    assert reg.get("help") is not None  # 外部改动不影响内部


def test_prefix_match():
    reg = CommandRegistry()
    for n in ["session", "session_list", "session_resume", "status", "memory"]:
        reg.register(_cmd(n))
    names = [c.name for c in reg.complete("/session")]
    assert names == ["session", "session_list", "session_resume"]
    assert [c.name for c in reg.complete("/s")] == [
        "session",
        "session_list",
        "session_resume",
        "status",
    ]
    # 空前缀 → 全部可见
    assert [c.name for c in reg.complete("")] == [
        "memory",
        "session",
        "session_list",
        "session_resume",
        "status",
    ]


def test_complete_excludes_hidden():
    reg = CommandRegistry()
    reg.register(_cmd("resume", hidden=True))
    reg.register(_cmd("review"))
    assert [c.name for c in reg.complete("/r")] == ["review"]
    assert reg.get("resume") is not None  # dispatcher 仍命中


def test_register_empty_name_raises():
    reg = CommandRegistry()
    with pytest.raises(ValueError):
        reg.register(_cmd(""))


# ── unregister / remove_by（ch11 T17，/skill reload/unload 用）──────────


def test_unregister_removes_name_and_aliases():
    """防 bug：unregister 按名移除时连别名键一起删（reload 后不残留影子命令）。"""
    reg = CommandRegistry()
    reg.register(_cmd("exit", aliases=("quit",)))
    assert reg.unregister("quit") is True
    assert reg.get("exit") is None
    assert reg.get("quit") is None


def test_unregister_missing_returns_false():
    """防 bug：unregister 未注册命令返回 False 不抛错。"""
    reg = CommandRegistry()
    assert reg.unregister("nonexistent") is False


def test_remove_by_predicate():
    """防 bug：remove_by 按谓词批量移除（remove_skill_commands 清 [skill] 命令）。"""
    reg = CommandRegistry()
    reg.register(_cmd("commit", description="提交 [skill]"))
    reg.register(_cmd("review", description="审查 [skill]"))
    reg.register(_cmd("help", description="帮助"))
    removed = reg.remove_by(lambda c: c.description.endswith("[skill]"))
    assert removed == 2
    assert reg.get("commit") is None
    assert reg.get("review") is None
    assert reg.get("help") is not None
