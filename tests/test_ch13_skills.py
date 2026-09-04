"""ch13 skill fork 底座统一测试（F10）。

防的 bug：
- _run_fork_agent 改用 run_to_completion 后 token 写回丢失（observer 必须聚合 usage）
- 达 maxTurns 时 skill fork 静默丢部分文本（应保留 exc.text）
- 改造破坏 ch11 fork 语义（结果/token 写回主对话行为不变）
"""

from __future__ import annotations

import pytest

from newcode.agent import Agent
from newcode.conversation.manager import ConversationManager
from newcode.provider.base import StreamEvent, TokenUsage
from newcode.tools.registry import Registry

pytestmark = pytest.mark.anyio


class SeqProvider:
    def __init__(self, text="fork-result"):
        self.text = text

    async def stream(self, payload):
        yield StreamEvent(text=self.text)
        yield StreamEvent(done=True, usage=TokenUsage(10, 5))


class DummyStore:
    def names(self):
        return []


class DummyCatalog:
    def get(self, name):
        return None


async def test_run_fork_agent_uses_run_to_completion_and_counts_tokens():
    """F10 核心：_run_fork_agent 走 run_to_completion（共用主循环）+ observer 聚合 token。"""
    from newcode.skills import executor as exec_mod

    agent = Agent(
        SeqProvider("out"),
        ConversationManager(20),
        Registry.default(),
        "s",
        "e",
        is_interactive=False,
    )
    text, in_t, out_t = await exec_mod.Executor(
        DummyCatalog(), DummyStore(), Registry.default(), SeqProvider()
    )._run_fork_agent(agent, "task")
    assert text == "out"
    assert in_t == 10 and out_t == 5


async def test_run_fork_agent_preserves_partial_text_on_max_turns():
    """达 maxTurns 保留部分文本（与 ch11 旧行为一致，不静默丢）。"""
    from newcode.skills import executor as exec_mod

    class MaxTurnsAgent(Agent):
        async def run_to_completion(
            self, task, *, already_injected=False, observer=None
        ):
            from newcode.subagent.errors import MaxTurnsReached

            raise MaxTurnsReached("partial", None, 2)

    agent = MaxTurnsAgent.__new__(MaxTurnsAgent)
    text, _in_t, _out_t = await exec_mod.Executor(
        DummyCatalog(), DummyStore(), Registry.default(), SeqProvider()
    )._run_fork_agent(agent, "task")
    assert text == "partial"
