"""ch10 集成测试（T11/T12）：启动冲突检测 / 分流 / /clear→/session_list 旧会话 / /review 流式 / 状态机。

防的 bug（每测试标注）：
- AC15 反向：正常注册不炸（启动无 panic）；重复注册必须在启动期抛错含冲突名；
- AC2 分流错误（/ 命令误触 Agent）；
- AC8：/clear 后旧会话仍可恢复（/session_list 可见）；
- AC9：/review 触发流式回合并落回 IDLE；
- N3a：非 idle 态注入 /clear 被拒。
"""

import asyncio

from rich.console import Console

from mewcode.agent.events import Event, EventType, StopReason
from mewcode.session.archive import SessionArchive
from mewcode.session.runtime import SessionRuntime
from mewcode.slash import CommandRegistry
from mewcode.slash.commands import register_all
from mewcode.tui.app import REPL, AppMode, SessionState


def _reg() -> CommandRegistry:
    reg = CommandRegistry()
    register_all(reg)
    return reg


def test_startup_conflict_detection():
    """防 AC15：注册期重复名字必须抛 RuntimeError 且消息含冲突名。"""
    reg = _reg()
    from mewcode.slash.registry import CommandDef, CommandKind

    duplicate = CommandDef(
        name="help",
        kind=CommandKind.LOCAL,
        handler=lambda ctx, args: None,
        description="dup",
    )
    try:
        reg.register(duplicate)
        raise AssertionError("应抛 RuntimeError")
    except RuntimeError as exc:
        assert "help" in str(exc)


def _make_repl(agent, runtime):
    """最小 REPL 桩：接真实 RichUIController（同 test_ch10_tui._make_repl）。"""
    from mewcode.tui.app import RichUIController

    repl = object.__new__(REPL)
    repl._console = Console(record=True, width=80)
    repl.command_registry = _reg()
    repl.ui = RichUIController(repl)
    repl.agent = agent
    repl.session_runtime = runtime
    repl.session_archive = None
    repl.memory_manager = None
    from mewcode.slash import CommandContext

    repl.command_ctx = CommandContext(
        registry=repl.command_registry,
        ui=repl.ui,
        agent=agent,
        conversation=None,
        plan_manager=None,
        session_runtime=runtime,
        session_archive=repl.session_archive,
    )
    repl.state = SessionState.IDLE
    repl.mode = AppMode.NORMAL
    repl._exit_requested = False
    repl._session_in_tokens = 0
    repl._session_out_tokens = 0
    repl._current_turn = 0
    repl._executing_slug = ""
    repl._pending_plan = ""
    repl._pending_slug = ""
    repl._stream_task = None
    return repl


class _NoRunAgent:
    """run 被调用即失败：断言 / 命令不触发 Agent。"""

    async def run(self, user_input, mode="normal", plan_content=""):
        raise AssertionError("Agent.run 不应被 / 命令触发")

    def cancel(self):
        pass


def test_dispatch_help_does_not_trigger_agent():
    """防 AC2：/help 分流到命令，不触发 Agent.run。"""
    agent = _NoRunAgent()
    repl = _make_repl(agent, None)
    assert asyncio.run(repl.dispatch_slash("/help")) is True


def test_plain_input_returns_false_for_agent_loop():
    """普通输入返回 False → 调用方走 AgentLoop。"""
    repl = _make_repl(_NoRunAgent(), None)
    assert asyncio.run(repl.dispatch_slash("帮我看看代码")) is False


def test_clear_then_session_list_shows_old(tmp_path):
    """防 AC8：/clear 换新会话后，/session_list 仍能看到旧会话为可恢复条目。"""
    runtime = SessionRuntime(str(tmp_path), max_turns=10, model="test-model")
    archive = SessionArchive(str(tmp_path))
    old_conv = runtime.create_new()
    old_id = runtime.session_id
    old_conv.add_user("第一条消息")  # 持久化到旧会话 writer

    agent = _NoRunAgent()
    agent.conv = old_conv
    agent.registry = type("R", (), {"count": lambda self: 3})()
    agent.permission = None
    agent.provider = None
    agent._context_mgr = None  # 未接线上下文管理 → reset_for_new_session 跳过

    repl = _make_repl(agent, runtime)
    repl.session_archive = archive
    repl.command_ctx.session_archive = archive

    ok = asyncio.run(repl.dispatch_slash("/clear"))
    assert ok is True
    assert runtime.session_id != old_id  # 新会话 id
    assert repl.mode == AppMode.NORMAL

    ids = [s.session_id for s in archive.list()]
    assert old_id in ids, "旧会话应作为可恢复条目出现"


class _StreamAgent:
    """run 产出固定事件流（DONE），并模拟真实 Agent.run 的 add_user 注入。"""

    def __init__(self):
        self.conv = None
        self.registry = None
        self.permission = None
        self.provider = None
        self._context_mgr = None

    async def run(self, user_input, mode="normal", plan_content=""):
        if self.conv is not None:
            self.conv.add_user(user_input)  # 模拟 Agent.run 的用户消息注入+持久化
        yield Event(EventType.TEXT, "审查中…")
        yield Event(EventType.DONE, StopReason.NATURAL)

    def cancel(self):
        pass


def test_skill_command_usage_hint(tmp_path):
    """ch11（原 AC9/N3 迁移，F6.4）：/review 已由 review Skill 接管。

    内置 /skill 本地命令无参显示用法提示，不触发 Agent（本地命令分流正确）。
    """
    runtime = SessionRuntime(str(tmp_path), max_turns=10, model="m")
    conv = runtime.create_new()
    agent = _NoRunAgent()
    agent.conv = conv
    repl = _make_repl(agent, runtime)
    repl.command_ctx.agent = agent

    ok = asyncio.run(repl.dispatch_slash("/skill"))
    assert ok is True
    assert "/skill <list|info|reload|load|unload>" in repl._console.export_text(
        clear=False
    )


def test_busy_clear_rejected():
    """防 N3a：STREAMING 态注入 /clear 被拒并提示等待。"""
    repl = _make_repl(_NoRunAgent(), None)
    repl.state = SessionState.STREAMING
    assert asyncio.run(repl.dispatch_slash("/clear")) is True
    assert "请等待当前任务完成" in repl._console.export_text(clear=False)
