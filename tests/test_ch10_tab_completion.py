"""Tab 补全单测（T10/F9）：SlashCompleter 候选 + REPL._handle_tab 单/多匹配行为。

防的 bug：
- 补全参与别名/描述匹配（F9.2 只按 name 前缀）；
- hidden 命令出现在候选（F9.5/AC14）；
- 非 IDLE 态 Tab 仍改输入（状态机破坏）；
- 单匹配补全不生效 / 多匹配未弹列表。
"""

from prompt_toolkit.completion import Completion
from prompt_toolkit.document import Document

from mewcode.slash import CommandRegistry
from mewcode.slash.commands import register_all
from mewcode.tui.app import REPL, SessionState, SlashCompleter


def _registry() -> CommandRegistry:
    reg = CommandRegistry()
    register_all(reg)
    return reg


def _repl_with(reg):
    repl = object.__new__(REPL)
    repl.command_registry = reg
    repl.state = SessionState.IDLE
    return repl


def _completions(repl, text: str) -> list[Completion]:
    completer = SlashCompleter(repl)
    return list(completer.get_completions(Document(text=text), None))


def test_completer_prefix_filters_names():
    repl = _repl_with(_registry())
    names = [c.text for c in _completions(repl, "/s")]
    assert names == [
        "session",
        "session_list",
        "session_new",
        "session_resume",
        "skill",
        "status",
    ]
    # 只匹配 name，不匹配 description/别名
    names_mem = [c.text for c in _completions(repl, "/mem")]
    assert names_mem == ["memory", "memory_add", "memory_clear", "memory_list"]


def test_completer_excludes_hidden():
    repl = _repl_with(_registry())
    names = [c.text for c in _completions(repl, "/r")]
    assert "resume" not in names  # /resume 隐藏
    # ch11：/review 已由 review Skill 接管（F6.4），内置补全不再含 review；/skill 可见
    assert "review" not in names
    skill_names = [c.text for c in _completions(repl, "/s")]
    assert "skill" in skill_names


def test_completer_ignores_non_slash():
    repl = _repl_with(_registry())
    assert _completions(repl, "hel") == []


def test_completer_shows_display():
    repl = _repl_with(_registry())
    c = _completions(repl, "/stat")[0]
    # Completion.display 被包成 FormattedText → 用 display_text 取纯文本
    assert (
        c.display_text == "/status  显示当前会话状态（模式/token/工具/记忆/模型/目录）"
    )


class _Buffer:
    def __init__(self, text=""):
        self.text = text
        self.cursor_position = 0
        self.complete_next_calls = 0

    def complete_next(self):
        self.complete_next_calls += 1


def test_tab_single_match_completes():
    repl = _repl_with(_registry())
    buf = _Buffer("/stat")
    repl._handle_tab(buf)
    assert buf.text == "/status "
    assert buf.cursor_position == len(buf.text)


def test_tab_multi_match_opens_menu():
    repl = _repl_with(_registry())
    buf = _Buffer("/session")
    repl._handle_tab(buf)
    assert buf.text == "/session"  # 不直接改输入
    assert buf.complete_next_calls == 1


def test_tab_no_match_noop():
    repl = _repl_with(_registry())
    buf = _Buffer("/zzz")
    repl._handle_tab(buf)
    assert buf.text == "/zzz"
    assert buf.complete_next_calls == 0


def test_tab_non_slash_noop():
    repl = _repl_with(_registry())
    buf = _Buffer("hello")
    repl._handle_tab(buf)
    assert buf.text == "hello"


def test_tab_not_idle_noop():
    repl = _repl_with(_registry())
    repl.state = SessionState.STREAMING
    buf = _Buffer("/stat")
    repl._handle_tab(buf)
    assert buf.text == "/stat"
    assert buf.complete_next_calls == 0
