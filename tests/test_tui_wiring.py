"""REPL 流式消费接线测试：验证 _consume_agent_events 真的调用渲染，而非只累积文本。

背景：曾出现 bug —— _consume_stream 只把 event.text 追加到 cur_reply，
从不渲染到终端，导致 TUI 看不到回复。此测试用 mock agent 驱动
REPL._consume_agent_events()，capture 输出断言渲染内容出现，防止回归。
"""

import asyncio

from rich.console import Console

from mewcode.tui.app import REPL
from mewcode.agent.events import Event, EventType
from mewcode.conversation.manager import ConversationManager


class FakeAgent:
    """mock agent：产出固定 Event 序列"""

    def __init__(self, events: list[Event]) -> None:
        self._events = events

    async def run(self, user_input: str):
        for e in self._events:
            yield e


def _make_repl(events: list[Event]) -> tuple[REPL, Console]:
    """绕过 __init__ 构造 REPL，返回 (repl, captured_console)"""
    repl = object.__new__(REPL)
    repl.agent = FakeAgent(events)
    repl.conv = ConversationManager("", 10)
    repl._stream_task = None
    repl.turn_start = 0.0
    repl.cur_reply = ""
    repl.state = None
    console = Console(record=True, width=60)
    repl._console = console
    return repl, console


def _run(events: list[Event]) -> str:
    repl, console = _make_repl(events)
    asyncio.run(repl._consume_agent_events("test"))
    return console.export_text()


def test_consume_stream_renders_text():
    """流式文本必须被渲染到终端输出"""
    events = [
        Event(EventType.TEXT, "你好，"),
        Event(EventType.TEXT, "我是**Mew**。"),
        Event(EventType.DONE, ""),
    ]
    output = _run(events)
    assert "你好" in output, f"渲染内容未出现在输出中: {output!r}"
    assert "Mew" in output, f"Markdown 加粗未渲染: {output!r}"


def test_consume_stream_adds_to_conversation():
    """done 后完整回复应记录到 cur_reply"""
    repl, console = _make_repl([
        Event(EventType.TEXT, "回复A"),
        Event(EventType.TEXT, "回复B"),
        Event(EventType.DONE, ""),
    ])
    asyncio.run(repl._consume_agent_events("test"))
    assert repl.cur_reply == "回复A回复B"


def test_consume_stream_no_done_event():
    """没有 DONE 时也应正常处理（mock 直接结束迭代）"""
    repl, console = _make_repl([Event(EventType.TEXT, "只有文本")])
    asyncio.run(repl._consume_agent_events("test"))
    # mock agent 无 DONE，_consume_agent_events 会因迭代结束而返回
    assert "只有文本" in console.export_text()


def test_consume_stream_retries_then_succeeds():
    """首次 ERROR 后应重试，第二次成功"""
    attempts = {"n": 0}

    class FlakyAgent(FakeAgent):
        async def run(self, user_input: str):
            if attempts["n"] == 0:
                attempts["n"] += 1
                yield Event(EventType.ERROR, RuntimeError("第一次失败"))
            else:
                attempts["n"] += 1
                yield Event(EventType.TEXT, "第二次成功")
                yield Event(EventType.DONE, "")

    repl, console = _make_repl([])
    repl.agent = FlakyAgent([])
    asyncio.run(repl._consume_agent_events("test"))
    assert attempts["n"] == 2
    assert "第二次成功" in console.export_text()


def test_tool_call_renders_tool_line():
    """TOOL_CALL 事件应渲染 Claude Code 风格工具行"""
    from mewcode.provider.base import ToolCall
    events = [
        Event(EventType.TOOL_CALL, ToolCall("read_file", {"path": "main.py"})),
        Event(EventType.TOOL_RESULT, ToolResult("ok", "import os")),
        Event(EventType.DONE, ""),
    ]
    output = _run(events)
    assert "● read_file" in output
    assert "import os" in output


from mewcode.provider.base import ToolResult
