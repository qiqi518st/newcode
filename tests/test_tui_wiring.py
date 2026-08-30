"""REPL 流式消费接线测试：验证 _consume_agent_events 真的调用渲染，而非只累积文本。

背景：曾出现 bug —— _consume_stream 只把 event.text 追加到 cur_reply，
从不渲染到终端，导致 TUI 看不到回复。此测试用 mock agent 驱动
REPL._consume_agent_events()，capture 输出断言渲染内容出现，防止回归。

ch04 更新：DONE payload 改为 StopReason 枚举，新增 TOKEN_USAGE/TURN_START/TURN_END 事件。
"""

import asyncio
import tempfile

from rich.console import Console

from mewcode.agent.events import Event, EventType, StopReason
from mewcode.plans import PlanManager
from mewcode.tui.app import REPL


class FakeAgent:
    """mock agent：产出固定 Event 序列"""

    def __init__(self, events: list[Event]) -> None:
        self._events = events

    def cancel(self) -> None:
        """ch04: Agent 需要 cancel 方法"""

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
    repl.plan_manager = PlanManager(tempfile.mkdtemp())
    repl._pending_plan = ""
    repl._pending_slug = ""
    repl._executing_slug = ""
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
    repl, _console = _make_repl(
        [
            Event(EventType.TEXT, "回复A"),
            Event(EventType.TEXT, "回复B"),
            Event(EventType.DONE, StopReason.NATURAL),
        ]
    )
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

        async def run(
            self, user_input: str, mode: str = "normal", plan_content: str = ""
        ):
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


def test_tool_result_with_rich_markup_does_not_crash():
    """工具结果含 Rich 标记语法（如 [/do <slug> / not now]）不应崩溃，按原文渲染

    背景：曾出现 bug —— 工具结果含 [bold] 或 [/do ...] 时，Rich 把 [] 当标记
    解析抛 MarkupError，且 _show_error 打印错误信息时再次崩溃，TUI 直接 traceback。
    此测试确保转义生效。
    """
    from mewcode.provider.base import ToolCall, ToolResult

    markup_content = "弹窗 [/do <slug> / not now] [bold]不会被解析[/bold]"
    events = [
        Event(EventType.TOOL_CALL, ToolCall("read_file", {"path": "x.txt"})),
        Event(EventType.TOOL_RESULT, ToolResult("ok", markup_content)),
        Event(EventType.DONE, StopReason.NATURAL),
    ]
    output = _run(events)
    # 内容按原文出现，[bold] 未被解析为加粗（否则会被剥离）
    assert "[/do <slug> / not now]" in output
    assert "[bold]不会被解析[/bold]" in output


def test_token_usage_accumulates():
    """TOKEN_USAGE 事件应累加到会话计数"""
    from mewcode.provider.base import TokenUsage

    repl, _console = _make_repl(
        [
            Event(EventType.TOKEN_USAGE, TokenUsage(100, 50)),
            Event(EventType.TOKEN_USAGE, TokenUsage(200, 80)),
            Event(EventType.DONE, StopReason.NATURAL),
        ]
    )
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


def test_ask_choice_arrow_navigation():
    """内联选项：↑/↓ 选择、Enter 确认、Esc 取消

    背景：曾用 radiolist_dialog 弹出独立对话框，用户要求改为在输入位置
    内联显示选项（类似 Claude Code 提问）。此测试验证按键交互逻辑。
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.styles import Style

    options = [
        ("/do create-hello.txt", "/do create-hello.txt — 执行此计划"),
        ("not now", "not now — 暂不执行，保留计划"),
    ]

    async def run_choice(keys: str, default_index: int = 1):
        repl = object.__new__(REPL)
        repl.mode = None
        repl.command_registry = None
        repl._session_in_tokens = 0
        repl._session_out_tokens = 0
        with create_pipe_input() as pipe:
            pipe.send_text(keys)
            session = PromptSession(
                input=pipe,
                output=DummyOutput(),
                style=Style.from_dict({}),
            )
            repl._choice_session = session
            return await repl._ask_choice("计划已保存", options, default_index)

    # 1. 默认选中 not now，直接回车 → not now
    assert asyncio.run(run_choice("\r")) == "not now"

    # 2. 按上（到 /do），回车 → /do slug
    assert asyncio.run(run_choice("\x1b[A\r")) == "/do create-hello.txt"

    # 3. Esc 取消 → None
    assert asyncio.run(run_choice("\x1b")) is None

    # 4. Ctrl+C 取消 → None
    assert asyncio.run(run_choice("\x03")) is None


def test_ask_multi_choice_space_toggle_navigation():
    """内联多选：↑/↓ 移动、空格 勾选/取消、Enter 确认、Esc 取消

    背景：/delete-plan 曾用 checkboxlist_dialog 弹独立窗，用户要求改为
    在输入位置内联（类似 Claude Code 提问）。此测试验证多选按键交互。
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.styles import Style

    options = [
        ("a-plan", "a-plan — plan A"),
        ("b-plan", "b-plan — plan B"),
        ("c-plan", "c-plan — plan C"),
    ]

    async def run_multi(keys: str):
        repl = object.__new__(REPL)
        repl.mode = None
        repl.command_registry = None
        repl._session_in_tokens = 0
        repl._session_out_tokens = 0
        with create_pipe_input() as pipe:
            pipe.send_text(keys)
            session = PromptSession(
                input=pipe, output=DummyOutput(), style=Style.from_dict({})
            )
            repl._choice_session = session
            return await repl._ask_multi_choice("选择：\n", options)

    # 1. 空格勾第一个，回车 → ['a-plan']
    assert asyncio.run(run_multi(" \r")) == ["a-plan"]

    # 2. 下移+空格 勾 b，下移+空格 勾 c，回车 → ['b','c']（顺序保留）
    result = asyncio.run(run_multi("\x1b[B \x1b[B \r"))
    assert sorted(result) == ["b-plan", "c-plan"]

    # 3. 无勾选直接回车 → []
    assert asyncio.run(run_multi("\r")) == []

    # 4. Esc 取消 → None
    assert asyncio.run(run_multi("\x1b")) is None

    # 5. 空格勾 a，空格取消 a，回车 → []
    assert asyncio.run(run_multi("  \r")) == []


def test_choice_session_pollution_regression():
    """弹窗的 key_bindings（Enter/ESC）不污染主循环的 prompt

    背景：曾出现严重 bug —— _ask_choice 与主循环共用 self._session，
    prompt_async 传的 key_bindings 会被 session 持久保留，导致弹窗结束后
    主循环按 Enter 仍被弹窗的 app.exit 捕获，返回旧 result 而非正常文本，
    表现为"执行计划后按回车又弹执行选项""/delete-plan 后回车退出程序"。
    修复：_ask_choice/_ask_multi_choice 改用独立 _choice_session。
    此测试验证：弹窗执行后，主 session 的 Enter 返回正常文本。
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.styles import Style

    async def main():
        with create_pipe_input() as pipe:
            main_session = PromptSession(
                input=pipe, output=DummyOutput(), style=Style.from_dict({})
            )
            # 弹窗用独立 session
            choice_session = PromptSession(
                input=pipe, output=DummyOutput(), style=Style.from_dict({})
            )
            repl = REPL.__new__(REPL)
            repl.mode = None
            repl.command_registry = None
            repl._session_in_tokens = 0
            repl._session_out_tokens = 0
            repl._choice_session = choice_session

            # 弹窗：按回车选默认项
            pipe.send_text("\r")
            r = await repl._ask_choice(
                "选：",
                [("A", "A"), ("B", "B")],
                default_index=0,
            )
            assert r == "A"

            # 主循环：按回车（空输入），应返回空字符串而非 "A"
            pipe.send_text("\r")
            main_result = await main_session.prompt_async(message="❯ ")
            assert main_result == "", f"主循环被弹窗 kb 污染：{main_result!r}"

    asyncio.run(main())
