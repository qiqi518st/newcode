"""ch13 Agent 扩展（run_to_completion / dont_ask / max_turns）测试。

防的 bug：
- run_to_completion 与 run 两套循环漂移（F5.2：必须共用主循环，此处驱动真实 run）
- max_turns 静默丢文本（达上限应抛 MaxTurnsReached 且携带最后文本）
- dont_ask 短路放行规则未命中的工具、但规则 DENY 仍拦（F5.3）
- already_injected=True 时重复注入任务 → 对话历史污染
"""

from __future__ import annotations

import tempfile
from typing import ClassVar

import pytest

from mewcode.agent import Agent
from mewcode.conversation.manager import ConversationManager
from mewcode.permission.checker import PermissionChecker
from mewcode.permission.modes import PermissionMode
from mewcode.provider.base import StreamEvent, TokenUsage, ToolCall, ToolResult
from mewcode.subagent.errors import MaxTurnsReached
from mewcode.tools.registry import Registry

pytestmark = pytest.mark.anyio


class SeqProvider:
    def __init__(self, script):
        self.script = script
        self.i = 0

    async def stream(self, payload):
        for ev in self.script[min(self.i, len(self.script) - 1)]:
            yield ev
        self.i += 1


def _txt(t):
    return [StreamEvent(text=t), StreamEvent(done=True, usage=TokenUsage(10, 5))]


def _tool(name, args):
    return [
        StreamEvent(tool_call=ToolCall(tool_name=name, arguments=args)),
        StreamEvent(done=True, usage=TokenUsage(10, 5)),
    ]


class WTool:
    name = "write_file"
    description = "w"
    parameters: ClassVar[dict] = {}
    read_only = False
    executed: ClassVar[list] = []

    async def execute(self, a):
        WTool.executed.append(a)
        return ToolResult(status="ok", output="w")


def _reg():
    reg = Registry()
    reg.register(WTool())
    return reg


async def test_run_to_completion_natural_returns_text():
    conv = ConversationManager(20)
    agent = Agent(SeqProvider([_txt("final")]), conv, _reg(), "s", "e", max_turns=10)
    out = await agent.run_to_completion("do it")
    assert out == "final"
    assert any(m.content == "do it" and m.role == "user" for m in conv.get_context())


async def test_run_to_completion_max_turns_raises_with_text():
    conv = ConversationManager(20)
    agent = Agent(
        SeqProvider(
            [
                _tool("write_file", {"path": "/x"}),
                [StreamEvent(text="mid"), *_tool("write_file", {"path": "/y"})],
            ]
        ),
        conv,
        _reg(),
        "s",
        "e",
        max_turns=2,
    )
    with pytest.raises(MaxTurnsReached) as exc:
        await agent.run_to_completion("do")
    assert exc.value.text == "mid" and exc.value.tool_count >= 2


async def test_run_to_completion_already_injected_no_duplicate():
    conv = ConversationManager(20)
    conv.add_user("pre-existing")
    agent = Agent(SeqProvider([_txt("r")]), conv, _reg(), "s", "e", max_turns=10)
    await agent.run_to_completion("in-conv", already_injected=True)
    user_msgs = [m.content for m in conv.get_context() if m.role == "user"]
    assert user_msgs == ["pre-existing"]


async def test_observer_counts():
    conv = ConversationManager(20)
    seen = []

    def obs(event):
        seen.append(event.type.value)

    agent = Agent(
        SeqProvider([_tool("write_file", {"path": "/x"}), _txt("ok")]),
        conv,
        _reg(),
        "s",
        "e",
        max_turns=10,
    )
    await agent.run_to_completion("go", observer=obs)
    assert "tool_call" in seen and "text" in seen


async def test_dont_ask_allows_ask_tool():
    with tempfile.TemporaryDirectory() as td:
        pc = PermissionChecker.create(td)
        sub = pc.for_subagent(PermissionMode.DEFAULT)
        WTool.executed = []
        conv = ConversationManager(20)
        in_proj = {"path": f"{td}/f.txt"}  # 项目内路径，过沙箱 → ASK → dont_ask 放行
        agent = Agent(
            SeqProvider([_tool("write_file", in_proj), _txt("ok")]),
            conv,
            _reg(),
            "s",
            "e",
            permission=sub,
            is_interactive=False,
            dont_ask=True,
        )
        await agent.run_to_completion("go")
        assert WTool.executed  # dont_ask 放行


async def test_no_dont_ask_denies_ask_tool():
    with tempfile.TemporaryDirectory() as td:
        pc = PermissionChecker.create(td)
        sub = pc.for_subagent(PermissionMode.DEFAULT)
        WTool.executed = []
        conv = ConversationManager(20)
        in_proj = {"path": f"{td}/f.txt"}
        agent = Agent(
            SeqProvider([_tool("write_file", in_proj), _txt("ok")]),
            conv,
            _reg(),
            "s",
            "e",
            permission=sub,
            is_interactive=False,
            dont_ask=False,
        )
        await agent.run_to_completion("go")
        assert not WTool.executed  # 非 dont_ask → ASK→DENY
