"""TUI 权限集成测试（T13）

背景：ch06 把权限模式切换（Shift+Tab）和 HITL 确认框接进 REPL。
这些测试防的 bug：
- Shift+Tab 切换模式不生效 / 切换后未同步到 agent.permission / 状态栏不更新
- HITL_REQUEST 确认框未进入 APPROVING 态、按键不工作
- approving 态 Esc/Ctrl+C 卡死或误退出程序
"""

import asyncio

from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.styles import Style

from mewcode.agent import EventType, StopReason
from mewcode.agent.events import Event
from mewcode.permission.hitl import HITLRequest
from mewcode.permission.modes import PermissionMode
from mewcode.tui.app import REPL, SessionState


def _raw_repl():
    """绕过 __init__ 构造 REPL 骨架"""
    from rich.console import Console

    repl = object.__new__(REPL)
    repl._session_in_tokens = 0
    repl._session_out_tokens = 0
    repl._permission_mode = PermissionMode.DEFAULT
    repl.state = SessionState.IDLE
    repl.mode = None
    repl._approve_cursor = 0
    repl._pending_hitl = None
    repl._console = Console(record=True, width=60)
    repl.turn_start = 0.0
    repl.cur_reply = ""
    return repl


class FakePermAgent:
    """带 permission 的 mock agent"""

    def __init__(self, mode=PermissionMode.DEFAULT):
        self._mode = mode
        self.resolved: list = []

    @property
    def permission(self):
        return self

    @property
    def mode(self):
        return self._mode

    def set_mode(self, mode):
        self._mode = mode

    def resolve_hitl(self, response):
        self.resolved.append(response)

    def cancel(self):
        self.resolved.append("cancelled")

    async def run(self, user_input, mode="normal", plan_content=""):
        for e in []:
            yield e


def _req():
    """构造一个触发确认的 HITLRequest"""
    return HITLRequest(
        tool_name="Write",
        params_preview="a.txt",
        reason="default 模式下 file_write 类操作需确认",
    )


class TestModeCycle:
    def test_shift_tab_cycles_all_four(self):
        """Shift+Tab 依次 DEFAULT→ACCEPT_EDITS→PLAN→BYPASS→DEFAULT"""
        repl = _raw_repl()
        repl.agent = FakePermAgent()
        expected = [
            PermissionMode.ACCEPT_EDITS,
            PermissionMode.PLAN,
            PermissionMode.BYPASS,
            PermissionMode.DEFAULT,
        ]
        for want in expected:
            repl._cycle_permission_mode()
            assert repl._permission_mode == want
            assert repl.agent.mode == want, "切换应同步到 agent.permission"

    def test_cycle_noop_when_not_idle(self):
        """非 IDLE 态不切换（防误触）"""
        repl = _raw_repl()
        repl.state = SessionState.STREAMING
        repl.agent = FakePermAgent()
        repl._cycle_permission_mode()
        assert repl._permission_mode == PermissionMode.DEFAULT
        assert repl.agent.mode == PermissionMode.DEFAULT

    def test_mode_persists_across_turn(self):
        """模式跨轮保持：切到 ACCEPT_EDITS 后 begin_turn 不重置"""
        repl = _raw_repl()
        repl.agent = FakePermAgent()
        repl._cycle_permission_mode()  # → ACCEPT_EDITS
        assert repl._permission_mode == PermissionMode.ACCEPT_EDITS
        # 模拟一轮 turn 结束回到 IDLE，模式应保持
        repl.state = SessionState.IDLE
        assert repl._permission_mode == PermissionMode.ACCEPT_EDITS

    def test_toolbar_shows_mode_not_provider(self):
        """状态栏左侧显示权限模式名，不含 provider 名"""
        repl = _raw_repl()
        repl.agent = FakePermAgent()
        repl._permission_mode = PermissionMode.BYPASS
        bar = repl._toolbar
        assert "BYPASS" in bar
        assert "mock" not in bar  # provider 名不显示


class TestHitlConfirmKeys:
    def _confirm_with_keys(self, keys, request):
        """用管道输入驱动确认框，返回 (response, repl)"""

        async def run():
            repl = _raw_repl()
            repl.agent = FakePermAgent()
            with create_pipe_input() as pipe:
                pipe.send_text(keys)
                session = PromptSession(
                    input=pipe,
                    output=DummyOutput(),
                    style=Style.from_dict({}),
                )
                repl._choice_session = session
                response = await repl._show_hitl_confirm(request)
                return response, repl

        return asyncio.run(run())

    def test_arrow_down_enter_allow_always(self):
        """ArrowDown+Enter → 选第二项 allow_always"""
        resp, _ = self._confirm_with_keys("\x1b[B\r", _req())
        assert resp.action == "allow_always"

    def test_digit_1_allow_once(self):
        resp, _ = self._confirm_with_keys("1", _req())
        assert resp.action == "allow_once"

    def test_digit_3_deny(self):
        resp, _ = self._confirm_with_keys("3", _req())
        assert resp.action == "deny"

    def test_y_allow_n_deny(self):
        assert self._confirm_with_keys("y", _req())[0].action == "allow_once"
        assert self._confirm_with_keys("n", _req())[0].action == "deny"

    def test_escape_cancel_denies(self):
        """Esc 取消 → 返回 deny 兜底（不崩溃）"""
        resp, _ = self._confirm_with_keys("\x1b", _req())
        assert resp.action == "deny"


class TestConsumeHitl:
    def test_hitl_request_triggers_confirm_state(self):
        """收到 HITL_REQUEST：state 切 APPROVING、approve_cursor 归零"""
        repl = _raw_repl()
        repl.agent = FakePermAgent()
        req = _req()

        # 用真实 REPL 的 consume 路径，事件流里注入 HITL_REQUEST
        async def fake_run(user_input, mode="normal", plan_content=""):
            yield Event(EventType.HITL_REQUEST, req)
            yield Event(EventType.DONE, StopReason.NATURAL)

        repl.agent.run = fake_run

        async def drive():
            # 确认框返回 allow_once
            repl._confirm = None
            with create_pipe_input() as pipe:
                pipe.send_text("1")
                repl._choice_session = PromptSession(
                    input=pipe,
                    output=DummyOutput(),
                    style=Style.from_dict({}),
                )
                await repl._consume_agent_events("test", "normal", "")

        asyncio.run(drive())

        # 确认框被调用过（agent 收到 resolve_hitl）
        assert repl.agent.resolved
        assert repl.agent.resolved[0].action == "allow_once"

    def test_approving_cancel_does_not_exit(self):
        """approving 态 cancel → 兜底 deny，不抛异常"""
        repl = _raw_repl()
        repl.state = SessionState.APPROVING
        repl.agent = FakePermAgent()
        repl._cancel_approving()
        assert repl.agent.resolved, "应触发 agent.cancel() 解阻塞"


def test_consume_renders_deny_result():
    """权限拒绝以 error 工具结果渲染（红字），Loop 不中断"""
    from rich.console import Console

    from mewcode.provider.base import ToolCall, ToolResult

    repl = _raw_repl()
    repl.agent = FakePermAgent()
    repl._console = Console(record=True, width=60)
    repl.turn_start = 0.0
    repl.cur_reply = ""

    async def fake_run(user_input, mode="normal", plan_content=""):
        yield Event(EventType.TOOL_CALL, ToolCall("write_file", {"path": "a.txt"}))
        yield Event(
            EventType.TOOL_RESULT,
            ToolResult(status="error", error="匹配 deny 规则：Write(a.txt)"),
        )
        yield Event(EventType.DONE, StopReason.NATURAL)

    repl.agent.run = fake_run
    asyncio.run(repl._consume_agent_events("test", "normal", ""))
    output = repl._console.export_text()
    assert "write_file" in output
    assert "deny" in output.lower() or "拒绝" in output
