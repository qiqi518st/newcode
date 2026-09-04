"""ch12 Agent 事件节点接线（spec F8.1/F7.4）：真实 Engine + 合成规则注入。

防的 bug：
- pre_tool_use 拦截若在权限检查之后，拦截信号会被权限引擎先弹审批（F7.6）——
  必须 hook 先行、拦截后跳过权限与真实执行（AC7）。
- turn_end 若在 CANCELLED/STREAM_ERROR 路径也触发，会误导 hook 统计（F3.1）。
- hook prompt 若不入 payload.reminders，或顺序在 plan reminder 之前，注入失效（F8.3/AC24）。
- 权限 DENY 分支若不触发 post_tool_use，post_tool_use 统计漏掉被拒调用（F3.1）。
- file_change 若在 write/edit 失败时也触发，格式化 hook 会对坏文件跑（spec 场景 1 要求 is_error=false）。
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from newcode.agent import Agent, EventType, StopReason
from newcode.conversation.manager import ConversationManager
from newcode.hooks.engine import Engine
from newcode.hooks.types import (
    Action,
    ActionType,
    Event,
    ExecutionResult,
    Hook,
    PromptAction,
)
from newcode.permission.types import CheckResult, Decision
from newcode.provider.base import StreamEvent, TokenUsage, ToolCall, ToolResult
from newcode.session.runtime import SessionRuntime
from newcode.tools.registry import Registry

pytestmark = pytest.mark.anyio


class MockWriteTool:
    name = "write_file"
    description = "write a file"
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }
    read_only = False
    executed: ClassVar[list[dict]] = []

    async def execute(self, arguments):
        MockWriteTool.executed.append(arguments)
        return ToolResult(status="ok", output="written")


class MockReadTool:
    name = "read_file"
    description = "read a file"
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }
    read_only = True

    async def execute(self, arguments):
        return ToolResult(status="ok", output="content")


class SequenceProvider:
    """按脚本序列产出流事件；每轮打标供 pre_send 断言 payload。"""

    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.last_reminders = []

    async def stream(self, payload):
        self.calls += 1
        self.last_reminders = list(payload.reminders)
        for ev in self.script[self.calls - 1]:
            yield ev


def _text(text="hello"):
    return [StreamEvent(text=text), StreamEvent(done=True, usage=TokenUsage(10, 5))]


def _tool(name, args):
    return [
        StreamEvent(tool_call=ToolCall(tool_name=name, arguments=args)),
        StreamEvent(done=True, usage=TokenUsage(10, 5)),
    ]


class RecordingExecutor:
    """记录 dispatch 调用的假执行器（真实 Engine 驱动 dispatch 编排，此处只替换动作执行）。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []  # (hook.name, payload)
        self.results: dict[str, ExecutionResult] = {}
        self.default = ExecutionResult()

    async def run(self, hook, payload, *, blocking):
        self.calls.append((hook.name, dict(payload)))
        return self.results.get(hook.name, self.default)


def _hook(name, event, prompt=None):
    return Hook(
        name=name,
        event=event,
        action=Action(
            type=ActionType.PROMPT,
            prompt=PromptAction(text=prompt or name),
        ),
    )


def _make(
    provider, registry, engine=None, runtime=None, permission=None, interactive=True
):
    conv = ConversationManager(20)
    return (
        Agent(
            provider,
            conv,
            registry,
            "stable",
            "env",
            permission=permission,
            is_interactive=interactive,
            hooks=engine,
            runtime=runtime,
        ),
        conv,
    )


def _engine_with(rec, rules):
    eng = Engine(rules=rules, sources=["t"])
    eng._executor = rec  # type: ignore[assignment]
    return eng


class TestAgentHookNodes:
    async def test_turn_start_and_turn_end_natural(self):
        """turn_start 在首轮前、turn_end 在 NATURAL 前触发（F8.1）。"""
        rec = RecordingExecutor()
        eng = _engine_with(
            rec, [_hook("ts", Event.TURN_START), _hook("te", Event.TURN_END)]
        )
        provider = SequenceProvider([_text()])
        agent, _ = _make(provider, Registry(), engine=eng)
        async for _ in agent.run("hi"):
            pass
        names = [c[0] for c in rec.calls]
        assert "ts" in names and "te" in names
        ts_payload = next(c for c in rec.calls if c[0] == "ts")[1]
        assert ts_payload["prompt"] == "hi"
        te_payload = next(c for c in rec.calls if c[0] == "te")[1]
        assert te_payload["iter"] == 1

    async def test_pre_send_has_prompt_and_last_user(self):
        """pre_send payload 含 prompt 与 conversation 末尾 user 消息（F3.4）。"""
        rec = RecordingExecutor()
        eng = _engine_with(rec, [_hook("ps", Event.PRE_SEND)])
        provider = SequenceProvider([_text()])
        agent, _ = _make(provider, Registry(), engine=eng)
        async for _ in agent.run("my question"):
            pass
        payload = next(c for c in rec.calls if c[0] == "ps")[1]
        assert payload["prompt"] == "my question"
        assert "my question" in payload["last_user_message"]

    async def test_post_receive_has_message(self):
        rec = RecordingExecutor()
        eng = _engine_with(rec, [_hook("pr", Event.POST_RECEIVE)])
        provider = SequenceProvider([_text("the reply")])
        agent, _ = _make(provider, Registry(), engine=eng)
        async for _ in agent.run("hi"):
            pass
        payload = next(c for c in rec.calls if c[0] == "pr")[1]
        assert "the reply" in payload["message"]

    async def test_no_turn_end_on_cancelled(self):
        """CANCELLED 不触发 turn_end（F3.1）。"""
        rec = RecordingExecutor()
        eng = _engine_with(rec, [_hook("te", Event.TURN_END)])
        registry = Registry()
        registry.register(MockReadTool())
        agent, _ = _make(
            SequenceProvider([_tool("read_file", {"path": "a.py"})]),
            registry,
            engine=eng,
        )
        # run 开头的 _cancelled.clear() 会清掉 run 前设置的取消信号，
        # 故在 stream 期间取消（走完一轮工具调用后在轮末检查 → DONE(CANCELLED)）
        provider = agent.provider

        class CancelWrapper:
            async def stream(self, payload):
                agent.cancel()
                async for ev in provider.stream(payload):
                    yield ev

        agent.provider = CancelWrapper()  # type: ignore[assignment]
        reasons = []
        async for e in agent.run("read"):
            if e.type == EventType.DONE:
                reasons.append(e.payload)
        assert StopReason.CANCELLED in reasons
        assert [c[0] for c in rec.calls if c[0] == "te"] == []


class TestPreToolUseIntercept:
    async def test_intercept_blocks_and_skips_permission(self):
        """pre_tool_use 拦截 → TOOL_CALL + TOOL_RESULT(error) + 权限不被调用 + 工具不执行（F7.4/AC7）。"""
        rec = RecordingExecutor()
        rec.results["block"] = ExecutionResult(blocked=True, reason="no write")
        eng = _engine_with(rec, [_hook("block", Event.PRE_TOOL_USE)])
        registry = Registry()
        registry.register(MockWriteTool())

        class FakePermission:
            calls = 0

            def check(self, tc, is_interactive=True, read_only=False):
                FakePermission.calls += 1
                return CheckResult(decision=Decision.ALLOW, reason="")

        provider = SequenceProvider(
            [_tool("write_file", {"path": "a.py", "content": "x"}), _text()]
        )
        MockWriteTool.executed = []
        agent, conv = _make(provider, registry, engine=eng, permission=FakePermission())
        events = [e async for e in agent.run("write a.py")]
        # TOOL_CALL + TOOL_RESULT(error)
        assert any(e.type == EventType.TOOL_CALL for e in events)
        tr_events = [e.payload for e in events if e.type == EventType.TOOL_RESULT]
        assert tr_events and tr_events[0].status == "error"
        assert "[hook block] no write" in tr_events[0].error
        # 权限未被调用 + 工具未执行
        assert FakePermission.calls == 0
        assert MockWriteTool.executed == []
        # 错误结果写入了历史
        assert len(conv.get_context()) > 0

    async def test_intercept_passes_through(self):
        """hook 放行 → 工具正常执行（AC6）。"""
        rec = RecordingExecutor()
        eng = _engine_with(rec, [_hook("pass", Event.PRE_TOOL_USE)])  # 默认放行
        registry = Registry()
        registry.register(MockWriteTool())
        provider = SequenceProvider([_tool("write_file", {"path": "a.py"}), _text()])
        MockWriteTool.executed = []
        agent, _ = _make(provider, registry, engine=eng)
        events = [e async for e in agent.run("write")]
        tr_events = [e.payload for e in events if e.type == EventType.TOOL_RESULT]
        assert tr_events and tr_events[0].status == "ok"
        assert MockWriteTool.executed == [{"path": "a.py"}]


class TestPromptInjection:
    async def test_hook_prompt_enters_reminders(self):
        """turn_start prompt 动作 → 下一轮 payload.reminders 含 hook-notification（F8.3/AC24）。"""
        rec = RecordingExecutor()
        rec.default = ExecutionResult(prompt="use zh-CN")
        eng = _engine_with(rec, [_hook("hint", Event.TURN_START)])
        provider = SequenceProvider([_text()])
        runtime = SessionRuntime(".")
        agent, _ = _make(provider, Registry(), engine=eng, runtime=runtime)
        async for _ in agent.run("hi"):
            pass
        contents = [getattr(m, "content", "") for m in provider.last_reminders]
        assert any("use zh-CN" in c for c in contents)
        assert any("<hook-notification>" in c for c in contents)


class TestPostToolUseAndFileChange:
    async def test_post_tool_use_on_success(self):
        """执行成功后 post_tool_use 触发（is_error=False）。"""
        rec = RecordingExecutor()
        eng = _engine_with(rec, [_hook("pt", Event.POST_TOOL_USE)])
        registry = Registry()
        registry.register(MockWriteTool())
        provider = SequenceProvider([_tool("write_file", {"path": "a.py"}), _text()])
        agent, _ = _make(provider, registry, engine=eng)
        async for _ in agent.run("write"):
            pass
        payload = next(c for c in rec.calls if c[0] == "pt")[1]
        assert payload["tool_name"] == "write_file"
        assert payload["is_error"] is False
        assert "written" in payload["tool_result"]

    async def test_post_tool_use_on_permission_deny(self):
        """权限 DENY 分支也触发 post_tool_use（is_error=True）（F3.1）。"""
        rec = RecordingExecutor()
        eng = _engine_with(rec, [_hook("pt", Event.POST_TOOL_USE)])
        registry = Registry()
        registry.register(MockWriteTool())

        class DenyPermission:
            def check(self, tc, is_interactive=True, read_only=False):
                return CheckResult(decision=Decision.DENY, reason="denied by policy")

        provider = SequenceProvider([_tool("write_file", {"path": "a.py"}), _text()])
        MockWriteTool.executed = []
        agent, _ = _make(provider, registry, engine=eng, permission=DenyPermission())
        async for _ in agent.run("write"):
            pass
        payload = next(c for c in rec.calls if c[0] == "pt")[1]
        assert payload["is_error"] is True
        assert "denied by policy" in payload["tool_result"]

    async def test_file_change_on_write_success(self):
        """write_file 成功后 file_change 触发（F3.1）。"""
        rec = RecordingExecutor()
        eng = _engine_with(rec, [_hook("fc", Event.FILE_CHANGE)])
        registry = Registry()
        registry.register(MockWriteTool())
        provider = SequenceProvider([_tool("write_file", {"path": "a.py"}), _text()])
        agent, _ = _make(provider, registry, engine=eng)
        async for _ in agent.run("write"):
            pass
        payload = next(c for c in rec.calls if c[0] == "fc")[1]
        assert payload["file_path"] == "a.py"

    async def test_no_file_change_on_error(self):
        """write/edit 失败不触发 file_change（spec 场景 1 条件 is_error=false）。"""
        rec = RecordingExecutor()
        eng = _engine_with(rec, [_hook("fc", Event.FILE_CHANGE)])

        class FailWriteTool(MockWriteTool):
            async def execute(self, arguments):
                return ToolResult(status="error", error="disk full")

        registry = Registry()
        registry.register(FailWriteTool())
        provider = SequenceProvider([_tool("write_file", {"path": "a.py"}), _text()])
        agent, _ = _make(provider, registry, engine=eng)
        async for _ in agent.run("write"):
            pass
        assert [c[0] for c in rec.calls if c[0] == "fc"] == []
