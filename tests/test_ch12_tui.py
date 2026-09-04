"""ch12 TUI 层事件接线（spec F7.5/F8.1）：user_prompt_submit 拦截 + 会话事件。

防的 bug：
- user_prompt_submit 拦截若在消息写历史之后，被拒消息会残留对话（F7.5/AC11）。
- 拦截提示 `[hook <name>]` 若不经 escape 会被 Rich 当 markup 吞掉，用户看不到原因。
- session_end/session_start 顺序颠倒会让 once 在错误时机重置（F2.2）。
- reset_for_new_session 若遗漏，/clear 后 once hook 不重新触发（AC9）。
"""

from __future__ import annotations

import pytest
from rich.console import Console

from newcode.hooks.engine import Engine
from newcode.hooks.types import (
    Action,
    ActionType,
    Event,
    ExecutionResult,
    Hook,
    PromptAction,
    ShellAction,
)
from newcode.slash import CommandRegistry
from newcode.slash.commands import register_all
from newcode.slash.context import CommandContext
from newcode.tui.app import REPL, AppMode, RichUIController, SessionState

pytestmark = pytest.mark.anyio


class _FakeAgent:
    def __init__(self):
        self.conv = object()
        self._context_mgr = None


class _FakeRuntime:
    def __init__(self):
        self.created = 0
        self.resets = 0

    def create_new(self):
        self.created += 1
        return object()

    async def resume(self, session_id):
        return object()

    async def reset_for_new_session(self):
        self.resets += 1

    def close(self):
        pass


class _RecordingExecutor:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def run(self, hook, payload, *, blocking):
        self.calls.append((hook.name, dict(payload)))
        return ExecutionResult()


def _make_repl(runtime=None, hooks_engine=None):
    reg = CommandRegistry()
    register_all(reg)
    agent = _FakeAgent()
    repl = object.__new__(REPL)
    repl._console = Console(record=True, width=80)
    repl.command_registry = reg
    repl.ui = RichUIController(repl)
    repl.agent = agent
    repl.session_runtime = runtime or _FakeRuntime()
    repl.session_archive = None
    repl.memory_manager = None
    repl.command_ctx = CommandContext(
        registry=reg,
        ui=repl.ui,
        agent=agent,
        conversation=None,
        plan_manager=None,
        hooks=hooks_engine,
    )
    repl.mode = AppMode.NORMAL
    repl.state = SessionState.IDLE
    repl._executing_slug = ""
    repl._pending_plan = ""
    repl._pending_slug = ""
    repl._session_in_tokens = 0
    repl._session_out_tokens = 0
    repl._current_turn = 0
    return repl


class TestUserPromptSubmit:
    async def test_blocked_prints_reason_and_stops(self):
        """user_prompt_submit 拦截 → 输入框下方提示 [hook <name>] <reason>，不启动 agent（AC11）。"""
        engine = Engine(
            rules=[
                Hook(
                    name="no-delete",
                    event=Event.USER_PROMPT_SUBMIT,
                    action=Action(
                        type=ActionType.COMMAND,
                        shell=ShellAction(
                            command='echo "prompt contains delete keyword" >&2; exit 2'
                        ),
                    ),
                )
            ],
            sources=["t"],
        )
        repl = _make_repl(hooks_engine=engine)
        agent_called = []

        async def spy_run_stream(user_input, mode, plan_content):
            agent_called.append(user_input)

        repl._run_stream = spy_run_stream  # type: ignore[assignment]
        await repl._process_input("请帮我 delete 那个文件")
        out = repl._console.export_text()
        assert "[hook no-delete] prompt contains delete keyword" in out
        assert agent_called == []  # 消息未启动 Agent、未进历史

    async def test_not_blocked_runs_agent(self):
        """条件不匹配 → 正常启动 Agent（F7.5 放行路径）。"""
        engine = Engine(
            rules=[
                Hook(
                    name="no-delete",
                    event=Event.USER_PROMPT_SUBMIT,
                    action=Action(
                        type=ActionType.PROMPT,
                        prompt=PromptAction(text="hint"),
                    ),
                )
            ],
            sources=["t"],
        )
        repl = _make_repl(hooks_engine=engine)
        agent_called = []

        async def spy_run_stream(user_input, mode, plan_content):
            agent_called.append((user_input, mode))

        async def spy_confirm():
            pass

        repl._run_stream = spy_run_stream  # type: ignore[assignment]
        repl._confirm_pending_plan = spy_confirm  # type: ignore[assignment]
        await repl._process_input("正常问题")
        assert agent_called == [("正常问题", "normal")]


class TestCommandExecute:
    async def test_command_execute_notified(self):
        """命中命令后 command_execute 通知（F8.1）。"""
        rec = _RecordingExecutor()
        engine = Engine(
            rules=[
                Hook(
                    name="ce",
                    event=Event.COMMAND_EXECUTE,
                    action=Action(
                        type=ActionType.PROMPT, prompt=PromptAction(text="x")
                    ),
                )
            ],
            sources=["t"],
        )
        engine._executor = rec  # type: ignore[assignment]
        repl = _make_repl(hooks_engine=engine)
        handled = await repl.dispatch_slash("/hooks")
        assert handled
        assert [c[0] for c in rec.calls] == ["ce"]
        assert rec.calls[0][1]["command"] == "hooks"


class TestSessionEvents:
    async def test_clear_session_sequence(self):
        """/clear：session_end（旧）→ reset → session_start（新）（F2.2/F8.1）。"""
        rec = _RecordingExecutor()
        engine = Engine(
            rules=[
                Hook(
                    name="se",
                    event=Event.SESSION_END,
                    action=Action(
                        type=ActionType.PROMPT, prompt=PromptAction(text="x")
                    ),
                ),
                Hook(
                    name="ss",
                    event=Event.SESSION_START,
                    action=Action(
                        type=ActionType.PROMPT, prompt=PromptAction(text="x")
                    ),
                ),
            ],
            sources=["t"],
        )
        engine._executor = rec  # type: ignore[assignment]
        runtime = _FakeRuntime()
        repl = _make_repl(runtime=runtime, hooks_engine=engine)
        await repl.ui.request_clear_session()
        assert [c[0] for c in rec.calls] == ["se", "ss"]
        assert runtime.created == 1 and runtime.resets == 1

    async def test_new_session_sequence(self):
        """/session_new 同 /clear（session_end → create_new → reset → session_start）。"""
        rec = _RecordingExecutor()
        engine = Engine(
            rules=[
                Hook(
                    name="se",
                    event=Event.SESSION_END,
                    action=Action(
                        type=ActionType.PROMPT, prompt=PromptAction(text="x")
                    ),
                ),
                Hook(
                    name="ss",
                    event=Event.SESSION_START,
                    action=Action(
                        type=ActionType.PROMPT, prompt=PromptAction(text="x")
                    ),
                ),
            ],
            sources=["t"],
        )
        engine._executor = rec  # type: ignore[assignment]
        runtime = _FakeRuntime()
        repl = _make_repl(runtime=runtime, hooks_engine=engine)
        await repl.ui.new_session()
        assert [c[0] for c in rec.calls] == ["se", "ss"]
        assert runtime.created == 1 and runtime.resets == 1

    async def test_resume_session_sequence(self):
        """/resume：session_end → resume → reset → session_resume。"""
        rec = _RecordingExecutor()
        engine = Engine(
            rules=[
                Hook(
                    name="se",
                    event=Event.SESSION_END,
                    action=Action(
                        type=ActionType.PROMPT, prompt=PromptAction(text="x")
                    ),
                ),
                Hook(
                    name="sr",
                    event=Event.SESSION_RESUME,
                    action=Action(
                        type=ActionType.PROMPT, prompt=PromptAction(text="x")
                    ),
                ),
            ],
            sources=["t"],
        )
        engine._executor = rec  # type: ignore[assignment]
        runtime = _FakeRuntime()
        repl = _make_repl(runtime=runtime, hooks_engine=engine)
        await repl.ui.resume_session("sess-1")
        assert [c[0] for c in rec.calls] == ["se", "sr"]
        assert runtime.resets == 1

    async def test_no_hooks_short_circuit(self):
        """未接线 hooks（command_ctx.hooks=None）时会话事件不报错（N10）。"""
        repl = _make_repl()  # hooks=None
        await repl.ui.request_clear_session()
        await repl.ui.new_session()
        assert True
