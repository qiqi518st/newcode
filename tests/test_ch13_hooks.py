"""ch13 hook agent 动作接通测试（F9.1/F9.4）。

防的 bug：
- agent 动作未注入 launcher 时抛错（应保持占位日志向后兼容）
- prompt 不渲染 {field} 模板 → hook 注入的任务缺上下文
- agent_name 无效时拦截主流程（F9.1：应 err 记日志不中断）
- agent 动作表达拦截信号（F9.4：成功必须不 blocked 不 err）
"""

from __future__ import annotations

import pytest

from newcode.hooks.engine import Engine
from newcode.hooks.types import (
    Action,
    ActionType,
    AgentAction,
    Event,
    Hook,
)

pytestmark = pytest.mark.anyio


def _engine(rules):
    return Engine(rules=rules, sources=["t"])


def _agent_hook(agent_name="explore", prompt="do {tool_name}"):
    return Hook(
        name="h",
        event=Event.SESSION_START,
        action=Action(
            type=ActionType.AGENT,
            agent=AgentAction(agent_name=agent_name, prompt=prompt),
        ),
    )


async def test_no_launcher_keeps_placeholder():
    eng = _engine([_agent_hook()])
    result = await eng.dispatch(Event.SESSION_START, {})
    assert result.blocked is False and result.injected_prompts == []


async def test_launcher_called_with_rendered_prompt():
    eng = _engine([_agent_hook()])
    calls = []

    async def launcher(name, prompt):
        calls.append((name, prompt))
        return "agent-x"

    eng.set_agent_launcher(launcher)
    result = await eng.dispatch(Event.SESSION_START, {"tool_name": "read_file"})
    assert calls == [("explore", "do read_file")]  # {field} 已渲染
    assert result.blocked is False  # 不表达拦截


async def test_unknown_agent_name_isolated():
    eng = _engine([_agent_hook(agent_name="bad")])

    async def bad(name, prompt):
        return None

    eng.set_agent_launcher(bad)
    # 不抛、不拦截；err 被引擎消化（stderr 日志）
    result = await eng.dispatch(Event.SESSION_START, {})
    assert result.blocked is False


async def test_launcher_exception_isolated():
    eng = _engine([_agent_hook()])

    async def explode(name, prompt):
        raise RuntimeError("boom")

    eng.set_agent_launcher(explode)
    result = await eng.dispatch(Event.SESSION_START, {})
    assert result.blocked is False
