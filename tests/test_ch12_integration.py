"""ch12 端到端集成（spec 端到端场景的自动化版本 + AC21 空引擎短路）。

流程：真实 YAML → loader.load → Engine → 注入 Agent（mock provider）→ 断言行为。

防的 bug：
- 三层合并顺序、坏 hook 隔离、once 重置这些跨模块行为，单独模块测试覆盖不到，
  集成层保证真实装配路径不漂移（对应 checklist E2E2/E2E3/E2E4 自动化断言）。
- 未配置任何 Hook 时 Agent 行为必须与 ch11 一致（N10/AC21）。
"""

from __future__ import annotations

import os
from typing import ClassVar

import pytest

from mewcode.agent import EventType
from mewcode.conversation.manager import ConversationManager
from mewcode.hooks import load
from mewcode.hooks.engine import Engine
from mewcode.hooks.types import (
    Action,
    ActionType,
    Event,
    ExecutionResult,
    Hook,
    PromptAction,
)
from mewcode.provider.base import StreamEvent, TokenUsage, ToolCall, ToolResult
from mewcode.session.runtime import SessionRuntime
from mewcode.tools.registry import Registry

pytestmark = pytest.mark.anyio


def _write_hooks(root, files: dict[str, str]):
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


class MockWriteTool:
    name = "write_file"
    description = "write"
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }
    read_only = False
    executed: ClassVar[list[dict]] = []

    async def execute(self, arguments):
        MockWriteTool.executed.append(arguments)
        return ToolResult(status="ok", output="written")


class _ToolProvider:
    """第一轮产出 write_file 调用，之后纯文本；记录所有轮的 reminders。"""

    def __init__(self):
        self.calls = 0
        self.all_reminders: list[str] = []

    async def stream(self, payload):
        self.calls += 1
        self.all_reminders.extend(getattr(m, "content", "") for m in payload.reminders)
        if self.calls == 1:
            yield StreamEvent(
                tool_call=ToolCall(tool_name="write_file", arguments={"path": "a.py"})
            )
            yield StreamEvent(done=True, usage=TokenUsage(10, 5))
        else:
            yield StreamEvent(text="done")
            yield StreamEvent(done=True, usage=TokenUsage(10, 5))


class TestIntegration:
    async def test_three_tier_merge_and_block(self, tmp_path, monkeypatch):
        """真实 YAML 三层合并 + pre_tool_use 拦截闭环（E2E2 自动化）。"""
        _write_hooks(
            tmp_path,
            {
                ".mewcode/config.local.yaml": (
                    "hooks:\n"
                    "  - name: block-rm\n"
                    "    event: pre_tool_use\n"
                    "    if:\n"
                    "      all_of:\n"
                    "        - field: tool_input.command\n"
                    '          match: {type: glob, value: "rm -rf *"}\n'
                    "    action: {type: command, command: \"echo 'dangerous: rm -rf' >&2; exit 2\"}\n"
                ),
                ".mewcode/config.yaml": (
                    "hooks:\n"
                    "  - name: hint\n"
                    "    event: turn_start\n"
                    '    action: {type: prompt, text: "hint {event}"}\n'
                ),
            },
        )
        monkeypatch.setattr(
            "mewcode.hooks.loader.HOOK_FILE_USER", str(tmp_path / "user.yaml")
        )
        eng = load(str(tmp_path))
        assert [r.name for r in eng.rules] == ["block-rm", "hint"]
        # 拦截闭环（真实 shell 动作）
        r = await eng.dispatch(
            Event.PRE_TOOL_USE, {"tool_input": {"command": "rm -rf /tmp/x"}}
        )
        assert (
            r.blocked
            and r.reason == "dangerous: rm -rf"
            and r.blocking_hook_name == "block-rm"
        )
        # 放行路径
        r2 = await eng.dispatch(Event.PRE_TOOL_USE, {"tool_input": {"command": "ls"}})
        assert not r2.blocked
        await eng.close()

    async def test_bad_hook_isolated(self, tmp_path, monkeypatch):
        """坏 hook（未知事件）不影响其余 hook 加载（F6.6/N1）。"""
        _write_hooks(
            tmp_path,
            {
                ".mewcode/config.yaml": (
                    "hooks:\n"
                    "  - name: bad\n"
                    "    event: UnknownEvent\n"
                    "    action: {type: prompt, text: x}\n"
                    "  - name: good\n"
                    "    event: turn_start\n"
                    "    action: {type: prompt, text: ok}\n"
                ),
            },
        )
        monkeypatch.setattr(
            "mewcode.hooks.loader.HOOK_FILE_USER", str(tmp_path / "user.yaml")
        )
        eng = load(str(tmp_path))
        assert [r.name for r in eng.rules] == ["good"]
        await eng.close()

    async def test_prompt_injection_and_once_reset(self, tmp_path, monkeypatch):
        """prompt 注入 reminder + once 语义 + /clear 重置（E2E3/AC8/AC9 自动化）。"""
        _write_hooks(
            tmp_path,
            {
                ".mewcode/config.yaml": (
                    "hooks:\n"
                    "  - name: once-hint\n"
                    "    event: turn_start\n"
                    "    once: true\n"
                    '    action: {type: prompt, text: "first only"}\n'
                ),
            },
        )
        monkeypatch.setattr(
            "mewcode.hooks.loader.HOOK_FILE_USER", str(tmp_path / "user.yaml")
        )
        eng = load(str(tmp_path))
        runtime = SessionRuntime(".")
        runtime.hook_engine = eng  # main.py 装配同样接线（reset 时清 once）
        registry = Registry()
        registry.register(MockWriteTool())
        provider = _ToolProvider()
        conv = ConversationManager(20)
        from mewcode.agent import Agent

        agent = Agent(provider, conv, registry, hooks=eng, runtime=runtime)
        MockWriteTool.executed = []
        async for _ in agent.run("write a.py"):
            pass
        # 首轮 reminder 含 once-hint 注入（hook-notification 标签）
        assert any("first only" in c for c in provider.all_reminders)
        # once：第二次 run 不再注入
        provider2 = _ToolProvider()
        agent2 = Agent(provider2, conv, registry, hooks=eng, runtime=runtime)
        async for _ in agent2.run("again"):
            pass
        assert not any("first only" in c for c in provider2.all_reminders)
        # 重置后再次注入
        await runtime.reset_for_new_session()
        provider3 = _ToolProvider()
        agent3 = Agent(provider3, conv, registry, hooks=eng, runtime=runtime)
        async for _ in agent3.run("third"):
            pass
        assert any("first only" in c for c in provider3.all_reminders)
        await eng.close()

    async def test_condition_all_of_combo(self):
        """all_of 条件组合求值（E2E 场景 1 条件形态）。"""
        rec = []

        class FakeExec:
            async def run(self, hook, payload, *, blocking):
                rec.append(payload)
                return ExecutionResult()

        eng = Engine(
            rules=[
                Hook(
                    name="fmt",
                    event=Event.POST_TOOL_USE,
                    action=Action(
                        type=ActionType.PROMPT, prompt=PromptAction(text="x")
                    ),
                )
            ],
            sources=["t"],
        )
        # 用条件过滤：is_error=false 才触发
        from mewcode.hooks.conditions import AtomCondition, Condition
        from mewcode.hooks.types import CombineMode
        from mewcode.permission.matcher import matcher_from_spec

        hook = eng.rules[0]
        hook.condition = Condition(
            CombineMode.ALL_OF,
            [
                AtomCondition(
                    "tool_name",
                    matcher_from_spec({"type": "exact", "value": "write_file"}),
                ),
                AtomCondition(
                    "is_error", matcher_from_spec({"type": "exact", "value": "false"})
                ),
            ],
        )
        eng._executor = FakeExec()  # type: ignore[assignment]
        await eng.dispatch(
            Event.POST_TOOL_USE, {"tool_name": "write_file", "is_error": False}
        )
        assert len(rec) == 1
        # 错误结果不触发
        await eng.dispatch(
            Event.POST_TOOL_USE, {"tool_name": "write_file", "is_error": True}
        )
        assert len(rec) == 1

    async def test_empty_engine_no_behavior_change(self):
        """未配置任何 Hook 时 Agent 行为与无 Hook 系统一致（N10/AC21）。"""
        eng = Engine(rules=[], sources=[])
        runtime = SessionRuntime(".")
        registry = Registry()
        registry.register(MockWriteTool())
        provider = _ToolProvider()
        conv = ConversationManager(20)
        from mewcode.agent import Agent

        agent = Agent(provider, conv, registry, hooks=eng, runtime=runtime)
        MockWriteTool.executed = []
        events = [e async for e in agent.run("write a.py")]
        assert any(e.type == EventType.TOOL_RESULT for e in events)
        assert MockWriteTool.executed == [{"path": "a.py"}]
        await eng.close()
