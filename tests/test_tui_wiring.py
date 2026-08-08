"""REPL 流式消费接线测试：验证 _consume_agent_events 真的调用渲染，而非只累积文本。

背景：曾出现 bug —— _consume_stream 只把 event.text 追加到 cur_reply，
从不渲染到终端，导致 TUI 看不到回复。此测试用 mock agent 驱动
REPL._consume_agent_events()，capture 输出断言渲染内容出现，防止回归。

ch04 更新：DONE payload 改为 StopReason 枚举，新增 TOKEN_USAGE/TURN_START/TURN_END 事件。
"""

import asyncio

from rich.console import Console

from mewcode.tui.app import REPL
from mewcode.agent.events import Event, EventType, StopReason
from mewcode.conversation.manager import ConversationManager


class FakeAgent:
    """mock agent：产出固定 Event 序列"""

    def __init__(self, events: list[Event]) -> None:
        self._events = events

    def cancel(self) -> None:
        """ch04: Agent 需要 cancel 方法"""
        pass

    async def run(self, user_input: str, mode: str = "normal", plan_content: str = ""):
        for e in self._events:
            yield e


def _make_repl(events: list[Event]) -> tuple[REPL, Console]:
    """绕过 __init__ 构造 REPL，返回 (repl, captured_console)"""
    repl = object.__new__(REPL)
    repl.agent = FakeAgent(events)
    repl._stream_task = None
    repl.turn_start = 0.0
    repl.cur_reply = ""
    repl.state = None
    repl.mode = None
    repl.plan_file = "plan.md"
    repl._session_in_tokens = 0
    repl._session_out_tokens = 0
    repl._current_turn = 0
    console = Console(record=True, width=60)
    repl._console = console
    return repl, console


def _run(events: list[Event]) -> str:
    repl, console = _make_repl(events)
    asyncio.run(repl._consume_agent_events("test", "normal", ""))
    return console.export_text()


def test_consume_stream_renders_text():
    """流式文本必须被渲染到终端输出"""
    events = [
        Event(EventType.TEXT, "你好，"),
        Event(EventType.TEXT, "我是**Mew**。"),
        Event(EventType.DONE, StopReason.NATURAL),
    ]
    output = _run(events)
    assert "你好" in output, f"渲染内容未出现在输出中: {output!r}"
    assert "Mew" in output, f"Markdown 加粗未渲染: {output!r}"


def test_consume_stream_adds_to_conversation():
    """done 后完整回复应记录到 cur_reply"""
    repl, console = _make_repl([
        Event(EventType.TEXT, "回复A"),
        Event(EventType.TEXT, "回复B"),
        Event(EventType.DONE, StopReason.NATURAL),
    ])
    asyncio.run(repl._consume_agent_events("test", "normal", ""))
    assert repl.cur_reply == "回复A回复B"


def test_consume_stream_no_done_event():
    """没有 DONE 时也应正常处理（mock 直接结束迭代）"""
    repl, console = _make_repl([Event(EventType.TEXT, "只有文本")])
    asyncio.run(repl._consume_agent_events("test", "normal", ""))
    # mock agent 无 DONE，_consume_agent_events 会因迭代结束而返回
    assert "只有文本" in console.export_text()


def test_consume_stream_retries_then_succeeds():
    """首次 ERROR 后应重试，第二次成功"""
    attempts = {"n": 0}

    class FlakyAgent(FakeAgent):
        def cancel(self):
            pass

        async def run(self, user_input: str, mode: str = "normal", plan_content: str = ""):
            if attempts["n"] == 0:
                attempts["n"] += 1
                yield Event(EventType.ERROR, RuntimeError("第一次失败"))
            else:
                attempts["n"] += 1
                yield Event(EventType.TEXT, "第二次成功")
                yield Event(EventType.DONE, StopReason.NATURAL)

    repl, console = _make_repl([])
    repl.agent = FlakyAgent([])
    asyncio.run(repl._consume_agent_events("test", "normal", ""))
    assert attempts["n"] == 2
    assert "第二次成功" in console.export_text()


def test_tool_call_renders_tool_line():
    """TOOL_CALL 事件应渲染 Claude Code 风格工具行"""
    from mewcode.provider.base import ToolCall, ToolResult
    events = [
        Event(EventType.TOOL_CALL, ToolCall("read_file", {"path": "main.py"})),
        Event(EventType.TOOL_RESULT, ToolResult("ok", "import os")),
        Event(EventType.DONE, StopReason.NATURAL),
    ]
    output = _run(events)
    assert "● read_file" in output
    assert "import os" in output


def test_token_usage_accumulates():
    """TOKEN_USAGE 事件应累加到会话计数"""
    from mewcode.provider.base import TokenUsage
    repl, console = _make_repl([
        Event(EventType.TOKEN_USAGE, TokenUsage(100, 50)),
        Event(EventType.TOKEN_USAGE, TokenUsage(200, 80)),
        Event(EventType.DONE, StopReason.NATURAL),
    ])
    asyncio.run(repl._consume_agent_events("test", "normal", ""))
    assert repl._session_in_tokens == 300
    assert repl._session_out_tokens == 130


def test_turn_start_renders_progress():
    """TURN_START 事件应渲染轮次进度"""
    events = [
        Event(EventType.TURN_START, 0),
        Event(EventType.TEXT, "hello"),
        Event(EventType.DONE, StopReason.NATURAL),
    ]
    output = _run(events)
    assert "Turn 1" in output


def test_done_with_stop_reason():
    """DONE 事件携带 StopReason 时应正确展示"""
    events = [
        Event(EventType.TEXT, "done"),
        Event(EventType.DONE, StopReason.MAX_TURNS),
    ]
    output = _run(events)
    assert "达到迭代上限" in output