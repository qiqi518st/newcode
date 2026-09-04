"""ch12 Hook 引擎（spec F2/F7/F9）：dispatch 编排 / once / 顺序短路 / 防重入。

防的 bug：
- 同一事件多个 hook 应按声明顺序执行，前一个拦截后后面的不得再执行（F7.3）。
- once 标记应只在"成功并执行"后记入；失败重跑、/clear 后重置（F2.2/AC9）。
- async hook 后台执行不得阻塞/参与拦截与 prompt 注入（F2.2）。
- hook 自身失败只记 stderr、绝不中断 dispatch（F9.1/AC21 空引擎短路）。
- 同事件 dispatch 期间嵌套 dispatch 会无限递归（如 file_change 格式化）——防重入（F9.2/AC20）。
- payload 通用字段 session_id/mode 应经 set_context_providers 注入（AC26）。
"""

from __future__ import annotations

import asyncio

import pytest

from newcode.hooks.engine import Engine
from newcode.hooks.types import (
    Action,
    ActionType,
    Event,
    ExecutionResult,
    Hook,
    PromptAction,
)

# 本文件全部为 async 测试（anyio 后端，见 conftest.py）
pytestmark = pytest.mark.anyio


def _hook(name, event, once=False, asyncio_mode=False):
    return Hook(
        name=name,
        event=event,
        action=Action(type=ActionType.PROMPT, prompt=PromptAction(text=name)),
        once=once,
        asyncio_mode=asyncio_mode,
    )


class _RecordingExecutor:
    """假执行器：记录调用并返回可编程结果。"""

    def __init__(self):
        self.calls: list[tuple[Hook, dict, bool]] = []
        self.results: dict[str, ExecutionResult] = {}
        self.default = ExecutionResult()

    async def run(self, hook, payload, *, blocking):
        self.calls.append((hook.name, payload, blocking))
        return self.results.get(hook.name, self.default)


class TestDispatch:
    def _make_engine(self, rules, executor=None):
        eng = Engine(rules=rules, sources=["t"])
        if executor is not None:
            eng._executor = executor  # type: ignore[assignment]
        return eng

    async def test_filter_by_event(self):
        """只分派匹配事件名的 hook（F8.2）。"""
        rec = _RecordingExecutor()
        eng = self._make_engine(
            [_hook("a", Event.TURN_START), _hook("b", Event.TURN_END)], rec
        )
        await eng.dispatch(Event.TURN_START, {})
        assert [c[0] for c in rec.calls] == ["a"]

    async def test_unconditional_matches(self):
        """无条件 hook（无 if）匹配该事件的所有 dispatch。"""
        rec = _RecordingExecutor()
        eng = self._make_engine([_hook("a", Event.TURN_START)], rec)
        await eng.dispatch(Event.TURN_START, {"prompt": "x"})
        assert rec.calls[0][1]["prompt"] == "x"
        assert rec.calls[0][1]["event"] == "turn_start"

    async def test_condition_filters(self):
        """条件不匹配则跳过该 hook（F4.6）。"""
        from newcode.hooks.conditions import AtomCondition, Condition
        from newcode.hooks.types import CombineMode
        from newcode.permission.matcher import matcher_from_spec

        rec = _RecordingExecutor()
        hook = _hook("a", Event.PRE_TOOL_USE)
        hook.condition = Condition(
            CombineMode.ALL_OF,
            [
                AtomCondition(
                    "tool_name",
                    matcher_from_spec({"type": "exact", "value": "write_file"}),
                )
            ],
        )
        eng = self._make_engine([hook], rec)
        await eng.dispatch(Event.PRE_TOOL_USE, {"tool_name": "read_file"})
        assert rec.calls == []
        await eng.dispatch(Event.PRE_TOOL_USE, {"tool_name": "write_file"})
        assert len(rec.calls) == 1

    async def test_once_only_first_success(self):
        """once：首次成功执行后记入，再次 dispatch 跳过；失败不记、重置清空（F2.2/AC9）。"""
        rec = _RecordingExecutor()
        hook = _hook("o", Event.TURN_START, once=True)
        eng = self._make_engine([hook], rec)
        await eng.dispatch(Event.TURN_START, {})
        await eng.dispatch(Event.TURN_START, {})
        assert len(rec.calls) == 1
        await eng.reset_for_new_session()
        await eng.dispatch(Event.TURN_START, {})
        assert len(rec.calls) == 2

    async def test_order_and_block_short_circuit(self):
        """顺序执行 + 拦截短路：前面 blocked 后面不执行（F7.3）。"""
        rec = _RecordingExecutor()
        rec.results["a"] = ExecutionResult(blocked=True, reason="no")
        eng = self._make_engine(
            [_hook("a", Event.PRE_TOOL_USE), _hook("b", Event.PRE_TOOL_USE)], rec
        )
        r = await eng.dispatch(Event.PRE_TOOL_USE, {})
        assert r.blocked and r.blocking_hook_name == "a" and r.reason == "no"
        assert [c[0] for c in rec.calls] == ["a"]

    async def test_prompt_collected(self):
        """prompt 动作累积进 injected_prompts（F8.3）。"""
        rec = _RecordingExecutor()
        rec.default = ExecutionResult(prompt="hint")
        eng = self._make_engine(
            [_hook("a", Event.TURN_START), _hook("b", Event.TURN_START)], rec
        )
        r = await eng.dispatch(Event.TURN_START, {})
        assert r.injected_prompts == ["hint", "hint"]

    async def test_blocking_blocks_not_on_notification_event(self):
        """拦截信号只在拦截类事件上生效（通知型事件 blocked 不拦截）。"""
        rec = _RecordingExecutor()
        rec.default = ExecutionResult(blocked=True, reason="x")
        eng = self._make_engine([_hook("a", Event.TURN_START)], rec)
        r = await eng.dispatch(Event.TURN_START, {})
        assert not r.blocked

    async def test_hook_failure_logged_not_raised(self, capsys):
        """hook 失败只记 stderr，不中断后续 hook（F9.1）。"""
        rec = _RecordingExecutor()
        rec.results["a"] = ExecutionResult(err=RuntimeError("boom"))
        eng = self._make_engine(
            [_hook("a", Event.TURN_START), _hook("b", Event.TURN_START)], rec
        )
        await eng.dispatch(Event.TURN_START, {})
        assert "[hook a] turn_start failed: boom" in capsys.readouterr().err
        assert [c[0] for c in rec.calls] == ["a", "b"]

    async def test_async_hook_background_not_blocking(self):
        """async hook 起后台任务不等结果；拦截类事件不存在 async（加载期拦截）。"""
        rec = _RecordingExecutor()
        # 通知型事件上的 async hook：立即返回，不参与 prompt 注入
        eng = self._make_engine([_hook("a", Event.TURN_START, asyncio_mode=True)], rec)
        r = await eng.dispatch(Event.TURN_START, {})
        assert r.injected_prompts == [] and not r.blocked
        await asyncio.sleep(0.05)
        assert len(rec.calls) == 1

    async def test_reentrancy_guard(self):
        """同事件 dispatch 期间嵌套 dispatch 不重入自身（F9.2/AC20）。"""
        calls = []

        async def run(hook, payload, *, blocking):
            calls.append(hook.name)
            # 动作执行中触发同事件嵌套 dispatch → 应被防重入拦截
            await eng.dispatch(Event.FILE_CHANGE, {})
            return ExecutionResult()

        eng = Engine(rules=[_hook("fmt", Event.FILE_CHANGE)], sources=["t"])
        eng._executor.run = run  # type: ignore[assignment]
        await eng.dispatch(Event.FILE_CHANGE, {})
        assert calls == ["fmt"]

    async def test_context_providers_inject_session_and_mode(self):
        """session_id/mode 经 set_context_providers 注入 payload（AC26）。"""
        rec = _RecordingExecutor()
        eng = self._make_engine([_hook("a", Event.TURN_START)], rec)
        eng.set_context_providers(lambda: "sess-1", lambda: "default")
        await eng.dispatch(Event.TURN_START, {"prompt": "x"})
        assert rec.calls[0][1]["session_id"] == "sess-1"
        assert rec.calls[0][1]["mode"] == "default"
        assert rec.calls[0][1]["event"] == "turn_start"

    async def test_empty_engine_short_circuits(self):
        """无规则时 dispatch 立即返回空结果（N10 空引擎开销近零）。"""
        eng = Engine(rules=[], sources=[])
        r = await eng.dispatch(Event.TURN_START, {})
        assert not r.blocked and r.injected_prompts == []

    async def test_cancelled_error_propagates(self):
        """dispatch 中取消 → CancelledError 传播（F9.3），不吞异常。"""

        class Boom(Exception):
            pass

        async def run(hook, payload, *, blocking):
            raise asyncio.CancelledError()

        eng = Engine(rules=[_hook("a", Event.TURN_START)], sources=["t"])
        eng._executor.run = run  # type: ignore[assignment]
        with pytest.raises(asyncio.CancelledError):
            await eng.dispatch(Event.TURN_START, {})
