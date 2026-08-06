"""REPL 流式消费接线测试：验证 _consume_stream 真的调用渲染，而非只累积文本。

背景：曾出现 bug —— _consume_stream 只把 event.text 追加到 cur_reply，
从不渲染到终端，导致 TUI 看不到回复。单次调用模式走 render_stream() 正常，
两条路径渲染逻辑分离，TUI 路径无人验证。此测试用 mock provider 驱动
REPL._consume_stream()，capture 输出断言渲染内容出现，防止回归。
"""

import asyncio

from rich.console import Console

from mewcode.tui.app import REPL
from mewcode.provider.base import StreamEvent
from mewcode.conversation.manager import ConversationManager


class FakeProvider:
    """mock provider：产出固定 StreamEvent 序列"""
    name = "fake"
    model = "fake-model"

    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = events

    def stream(self, msgs):
        async def _gen():
            for e in self._events:
                yield e
        return _gen()


def _make_repl(events: list[StreamEvent]) -> tuple[REPL, Console]:
    """绕过 __init__ 构造 REPL（避免 PromptSession 需要真实终端），返回 (repl, captured_console)"""
    repl = object.__new__(REPL)
    repl.provider = FakeProvider(events)
    repl.conv = ConversationManager("", 10)
    repl._stream_task = None
    repl.turn_start = 0.0
    repl.cur_reply = ""
    repl.state = None
    console = Console(record=True, width=60)
    repl._console = console
    return repl, console


def _run(events: list[StreamEvent]) -> str:
    repl, console = _make_repl(events)
    asyncio.run(repl._consume_stream())
    return console.export_text()


def test_consume_stream_renders_text():
    """流式文本必须被渲染到终端输出"""
    events = [StreamEvent(text="你好，"), StreamEvent(text="我是**Mew**。"), StreamEvent(done=True)]
    output = _run(events)
    assert "你好" in output, f"渲染内容未出现在输出中: {output!r}"
    assert "Mew" in output, f"Markdown 加粗未渲染: {output!r}"


def test_consume_stream_adds_to_conversation():
    """done 后完整回复应追加到对话历史"""
    repl, console = _make_repl([
        StreamEvent(text="回复A"),
        StreamEvent(text="回复B"),
        StreamEvent(done=True),
    ])
    asyncio.run(repl._consume_stream())
    ctx = repl.conv.get_context()
    assert any("回复A回复B" in m.content for m in ctx if m.role == "assistant")


def test_consume_stream_no_done_event():
    """stream 结束但无 done 事件时也应追加并完成"""
    repl, console = _make_repl([StreamEvent(text="只有文本")])
    asyncio.run(repl._consume_stream())
    assert any("只有文本" in m.content for m in repl.conv.get_context() if m.role == "assistant")


def test_consume_stream_retries_then_succeeds():
    """首次 err 后应重试，第二次成功"""
    attempts = {"n": 0}

    class FlakyProvider(FakeProvider):
        def stream(self, msgs):
            async def _gen():
                if attempts["n"] == 0:
                    attempts["n"] += 1
                    yield StreamEvent(err=RuntimeError("第一次失败"))
                else:
                    attempts["n"] += 1
                    yield StreamEvent(text="第二次成功")
                    yield StreamEvent(done=True)
            return _gen()

    repl, console = _make_repl([])
    repl.provider = FlakyProvider([])
    asyncio.run(repl._consume_stream())
    assert attempts["n"] == 2
    assert "第二次成功" in console.export_text()