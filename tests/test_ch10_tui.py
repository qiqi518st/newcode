"""TUI 接入测试（T10/T12）：object.__new__(REPL) + mock 驱动 dispatch_slash 真实代码路径。

防的 bug：
- "/" 命令误触发 Agent.run（AC2 分流错误）；普通输入不走 AgentLoop（分流错误）；
- 退化输入 "/"、"/ /help" 拼出 `"未知命令: /"` 悬空斜杠；
- 大小写敏感；KindUI/KindPrompt 非 idle 未拒（N3a）；/clear 原子重置顺序错乱（AC8）；
- /exit 未置退出标志导致 run() 循环继续。
"""

import asyncio

from rich.console import Console

from mewcode.plans import PlanManager
from mewcode.slash import CommandContext, CommandRegistry
from mewcode.slash.commands import register_all
from mewcode.tui.app import REPL, AppMode, SessionState


class _FakeAgent:
    """mock agent：run 若被调用即失败（用于断言"/ 命令不触发 Agent"）。"""

    def __init__(self, fail_on_run: bool = True):
        self.fail_on_run = fail_on_run
        self.conv = None
        self._context_mgr = None
        self.provider = None
        self.permission = None
        self.registry = type("R", (), {"count": lambda self: 3})()

    async def run(self, user_input, mode="normal", plan_content=""):
        if self.fail_on_run:
            raise AssertionError("Agent.run 不应被 / 命令触发")
        if False:  # 保持 async generator 形态（async for 需要）
            yield None

    def cancel(self):
        pass


class _FakeRuntime:
    def __init__(self):
        self.context = None
        self.writer = None
        self.conversation = None
        self.created = 0

    def create_new(self):
        self.created += 1
        self.conversation = object()
        return self.conversation

    async def resume(self, session_id):
        return None

    def close(self):
        pass


def _make_repl(state=SessionState.IDLE, agent=None, runtime=None):
    """构造 dispatch_slash 可用但未启动 PromptSession 的 REPL 桩；接真实 RichUIController。

    object.__new__ 跳过 __init__：需把 RichUIController 访问的 repl.* 属性补齐。
    """
    from mewcode.tui.app import RichUIController

    reg = CommandRegistry()
    register_all(reg)
    agent = agent or _FakeAgent()
    runtime = runtime or _FakeRuntime()
    repl = object.__new__(REPL)
    repl._console = Console(record=True, width=80)
    repl.command_registry = reg
    repl.ui = RichUIController(repl)
    repl.agent = agent
    repl.session_runtime = runtime
    repl.session_archive = None
    repl.memory_manager = None
    repl.command_ctx = CommandContext(
        registry=reg,
        ui=repl.ui,  # 真实 RichUIController：命令操作走真实接线
        agent=agent,
        conversation=None,
        plan_manager=PlanManager.__new__(PlanManager),
        session_runtime=runtime,
        session_archive=None,
    )
    repl.state = state
    repl.mode = AppMode.NORMAL
    repl._exit_requested = False
    repl._session_in_tokens = 10
    repl._session_out_tokens = 5
    repl._current_turn = 1
    repl._executing_slug = ""
    repl._pending_plan = ""
    repl._pending_slug = ""
    repl._stream_task = None
    return repl


def _console_text(repl) -> str:
    # Rich export_text 默认 clear=True 会清空记录缓冲 → clear=False 保持幂等
    return repl._console.export_text(clear=False)


def test_slash_command_does_not_touch_agent():
    repl = _make_repl()
    ok = asyncio.run(repl.dispatch_slash("/help"))
    assert ok is True


def test_plain_input_returns_false():
    repl = _make_repl()
    assert asyncio.run(repl.dispatch_slash("你好，世界")) is False
    assert asyncio.run(repl.dispatch_slash("  ")) is False


def test_unknown_command_friendly():
    repl = _make_repl()
    assert asyncio.run(repl.dispatch_slash("/foobar")) is True
    text = _console_text(repl)
    assert "未知命令" in text
    assert "/help" in text


def test_degenerate_slash_no_dangling():
    """ "/"、"/ /help" 不出现 `未知命令: /,` 悬空斜杠（T3 约定）。"""
    for bad in ("/", "/ /help", "//double"):
        repl = _make_repl()
        assert asyncio.run(repl.dispatch_slash(bad)) is True
        text = repl._console.export_text()
        assert "未知命令: /" not in text, f"{bad!r} 拼出悬空斜杠: {text!r}"


def test_case_insensitive():
    repl = _make_repl()
    assert asyncio.run(repl.dispatch_slash("/Help")) is True
    assert "未知命令" not in repl._console.export_text()


def test_help_lists_all_builtins():
    repl = _make_repl()
    asyncio.run(repl.dispatch_slash("/help"))
    text = _console_text(repl)
    for name in [
        "help",
        "status",
        "memory",
        "permission",
        "session",
        "plan",
        "do",
        "clear",
        "compact",
        "skill",
        "exit",
    ]:
        assert f"/{name}" in text
    assert "/resume" not in text  # hidden 不列出


def test_args_on_noarg_command_is_miss():
    """未声明参数的命令带参数 → 按未命中处理（F7.2）。"""
    repl = _make_repl()
    assert asyncio.run(repl.dispatch_slash("/help xx")) is True
    assert "未知命令" in repl._console.export_text()


def test_busy_gates_ui_and_prompt_commands():
    repl = _make_repl(state=SessionState.STREAMING)
    for cmd in ("/clear", "/compact", "/do", "/plan", "/resume"):
        r = asyncio.run(repl.dispatch_slash(cmd))
        assert r is True
        assert "请等待当前任务完成" in repl._console.export_text()


def test_local_allowed_while_busy():
    repl = _make_repl(state=SessionState.STREAMING)
    assert asyncio.run(repl.dispatch_slash("/status")) is True
    assert "请等待" not in _console_text(repl)
    assert "Mode" in _console_text(repl)


def test_exit_sets_flag():
    repl = _make_repl()
    asyncio.run(repl.dispatch_slash("/exit"))
    assert repl._exit_requested is True


def test_clear_atomic_reset():
    """/clear 原子重置：新会话 + agent/context 重指向 + token/回合归零 + AppMode NORMAL（AC8）。"""
    agent = _FakeAgent(fail_on_run=False)
    reset_calls = []

    class _CtxMgr:
        def reset_for_new_session(self, conv):
            reset_calls.append(conv)

    agent._context_mgr = _CtxMgr()
    runtime = _FakeRuntime()
    repl = _make_repl(agent=agent, runtime=runtime)
    repl.mode = AppMode.PLAN
    repl.state = SessionState.IDLE

    ok = asyncio.run(repl.dispatch_slash("/clear"))
    assert ok is True
    assert runtime.created == 1
    assert agent.conv is runtime.conversation  # agent 重指向新会话
    assert reset_calls and reset_calls[0] is runtime.conversation  # context_mgr 重指向
    assert repl._session_in_tokens == 0
    assert repl._session_out_tokens == 0
    assert repl._current_turn == 0
    assert repl.mode == AppMode.NORMAL
    assert "已清空当前会话" in _console_text(repl)


def test_handler_exception_does_not_crash():
    """handler 抛异常 → dispatch 上屏"命令执行失败"且返回 True（F6 兜底）。"""
    from mewcode.slash.registry import CommandDef, CommandKind

    async def boom(ctx, args):
        raise RuntimeError("boom")

    repl = _make_repl()
    repl.command_registry.register(
        CommandDef(name="boom", kind=CommandKind.LOCAL, handler=boom, description="x")
    )
    assert asyncio.run(repl.dispatch_slash("/boom")) is True
    assert "命令执行失败: boom" in _console_text(repl)
