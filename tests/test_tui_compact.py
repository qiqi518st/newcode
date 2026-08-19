"""TUI command and context-compaction integration coverage for ch08."""

import asyncio
import tempfile

from rich.console import Console

from mewcode.agent.events import Event, EventType, StopReason
from mewcode.context.summarize import CompactOutcome
from mewcode.permission.modes import PermissionMode
from mewcode.plans import PlanManager
from mewcode.tools.registry import Registry
from mewcode.tui.app import REPL, AppMode, SessionState


class CompactAgent:
    def __init__(self, outcome):
        self.registry = Registry()
        self.outcome = outcome
        self.compact_calls = 0
        self.run_calls = 0
        self.permission = None

    async def run_force_compact(self, tool_defs):
        self.compact_calls += 1
        return self.outcome

    async def run(self, user_input, mode="normal", plan_content=""):
        self.run_calls += 1
        yield Event(EventType.TEXT, "unexpected")
        yield Event(EventType.DONE, StopReason.NATURAL)

    def cancel(self):
        pass


def make_repl(agent):
    repl = object.__new__(REPL)
    repl.agent = agent
    repl._console = Console(record=True, width=80)
    repl.state = SessionState.IDLE
    repl.mode = AppMode.NORMAL
    repl._permission_mode = PermissionMode.DEFAULT
    repl.cur_reply = ""
    repl.turn_start = 0.0
    repl._stream_task = None
    repl.plan_manager = PlanManager(tempfile.mkdtemp())
    repl._pending_plan = ""
    repl._pending_slug = ""
    repl._executing_slug = ""
    repl._session_in_tokens = 0
    repl._session_out_tokens = 0
    repl._current_turn = 0
    repl._retry_count = 0
    return repl


def test_compact_routes_to_agent_without_normal_run():
    outcome = CompactOutcome(True, 100, 40, 0, True, "", [])
    agent = CompactAgent(outcome)
    repl = make_repl(agent)

    asyncio.run(repl._handle_compact())

    assert agent.compact_calls == 1
    assert agent.run_calls == 0
    assert repl.state == SessionState.IDLE


def test_compact_shows_token_change():
    repl = make_repl(CompactAgent(CompactOutcome(True, 123, 45, 0, True, "", [])))

    asyncio.run(repl._handle_compact())

    output = repl._console.export_text()
    assert "123" in output and "45" in output


def test_unknown_command_is_friendly_and_does_not_call_agent():
    agent = CompactAgent(CompactOutcome(True, 1, 1, 0, True, "", []))
    repl = make_repl(agent)

    asyncio.run(repl._process_input("/unknown"))

    output = repl._console.export_text()
    assert "未知命令" in output
    assert "/compact" in output
    assert agent.run_calls == 0


def test_compacting_event_shows_progress_prompt():
    repl = make_repl(CompactAgent(CompactOutcome(True, 1, 1, 0, True, "", [])))

    async def _events():
        yield Event(EventType.CONTEXT_COMPACTING, "auto")
        yield Event(EventType.TEXT, "ok")
        yield Event(EventType.DONE, StopReason.NATURAL)

    class EventAgent:
        permission = None

        async def run(self, *args, **kwargs):
            async for event in _events():
                yield event

    async def consume():
        repl.agent = EventAgent()
        await repl._consume_agent_events("hello", "normal", "")

    asyncio.run(consume())

    assert "正在压缩上下文" in repl._console.export_text()


def test_context_events_use_prominent_white_marker():
    repl = make_repl(CompactAgent(CompactOutcome(True, 1, 1, 1, True, "", [])))

    async def _events():
        yield Event(EventType.CONTEXT_OFFLOADED, {"count": 3, "spill_dir": "spill"})
        yield Event(
            EventType.CONTEXT_COMPACTED,
            CompactOutcome(True, 100, 40, 3, True, "", []),
        )
        yield Event(EventType.DONE, StopReason.NATURAL)

    class EventAgent:
        permission = None

        async def run(self, *args, **kwargs):
            async for event in _events():
                yield event

    async def consume():
        repl.agent = EventAgent()
        await repl._consume_agent_events("hello", "normal", "")

    asyncio.run(consume())

    output = repl._console.export_text()
    assert "○ 大结果已落盘" in output
    assert "○ 上下文压缩完成" in output


def test_plan_and_normal_commands_still_migrate_modes():
    repl = make_repl(CompactAgent(CompactOutcome(True, 1, 1, 0, True, "", [])))

    asyncio.run(repl._process_input("/plan"))
    assert repl.mode == AppMode.PLAN
    asyncio.run(repl._process_input("/normal"))
    assert repl.mode == AppMode.NORMAL
