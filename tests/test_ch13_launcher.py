"""ch13 subagent/launcher.py 统一启动器测试。

防的 bug：
- 前台超时用 asyncio.wait_for → 超时会 cancel 子 Agent（杀重来，F7.3）——必须 asyncio.wait
- Fork 误走前台 / 后台总闸关闭时 Fork 不报错（F11.1/AC27）
- build_sub_registry 漏层（agent 工具残留 / 白名单黑名单组合错）
- 模型分层缺配置不降级父模型
"""

from __future__ import annotations

import asyncio
import tempfile

import pytest

from mewcode.agent import Agent
from mewcode.conversation.manager import ConversationManager
from mewcode.permission.checker import PermissionChecker
from mewcode.provider.base import StreamEvent, TokenUsage
from mewcode.subagent.config import AgentConfig
from mewcode.subagent.launcher import SubAgentLauncher
from mewcode.subagent.manager import Status, TaskManager
from mewcode.subagent.types import AgentDefinition, Source
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


def _txt(t="r"):
    return [StreamEvent(text=t), StreamEvent(done=True, usage=TokenUsage(10, 5))]


class HangProvider:
    async def stream(self, payload):
        await asyncio.Event().wait()
        yield


class StubCatalog:
    def __init__(self):
        self.roles = {
            "explore": AgentDefinition(
                name="explore",
                description="d",
                body="你是探索者",
                disallowed_tools=["execute_command"],
                max_turns=5,
                source=Source.BUILTIN,
            )
        }

    def resolve(self, name):
        return self.roles.get(name)

    def fork_definition(self):
        return AgentDefinition(
            name="__fork__", description="fork", background=True, source=Source.BUILTIN
        )


def _make(parent_provider, *, cfg=None, parent=None, catalog=None, manager=None):
    reg = Registry.default()
    if parent is None:
        parent = Agent(
            SeqProvider([_txt("parent")]),
            ConversationManager(20),
            reg,
            "stable",
            "env",
            permission=PermissionChecker.create(tempfile.mkdtemp()),
        )
    mgr = manager or TaskManager()
    return (
        SubAgentLauncher(
            parent_provider,
            None,
            parent.permission,
            None,
            catalog or StubCatalog(),
            mgr,
            cfg or AgentConfig(async_timeout_s=0.2),
            lambda: parent,
        ),
        parent,
        mgr,
    )


async def test_build_sub_registry_filters():
    launcher, _, _ = _make(SeqProvider([_txt()]))
    role = StubCatalog().resolve("explore")
    names = launcher.build_sub_registry(role, is_background=False).names()
    assert "execute_command" not in names  # 黑名单
    assert "agent" not in names  # 全局禁止
    assert "read_file" in names


async def test_foreground_completion_returns_text():
    launcher, _, _ = _make(SeqProvider([_txt("sub-result")]))
    res = await launcher.launch_defined("explore", "查一下")
    assert res.status == "completed" and res.text == "sub-result"


async def test_background_launch():
    launcher, _, mgr = _make(SeqProvider([_txt()]))
    res = await launcher.launch_defined("explore", "x", background=True)
    assert res.status == "async_launched"
    await asyncio.sleep(0.05)
    assert mgr.get(res.task_id).status == Status.COMPLETED


async def test_unknown_role_error():
    launcher, _, _ = _make(SeqProvider([_txt()]))
    res = await launcher.launch_defined("nope", "x")
    assert "未知 subagent_type" in res.error


async def test_foreground_timeout_adopts_without_kill():
    # HangProvider 子 Agent 永不完成 → 超时移交后台，任务不被杀（mock 证明：仍在 RUNNING）
    launcher, _, mgr = _make(HangProvider(), cfg=AgentConfig(async_timeout_s=0.1))
    res = await launcher.launch_defined("explore", "long")
    assert res.status == "timed_out_to_background"
    bt = mgr.get(res.task_id)
    assert bt is not None and bt.adopted is True and bt.status == Status.RUNNING


async def test_fork_background_with_boilerplate():
    launcher, parent, mgr = _make(SeqProvider([_txt()]), cfg=AgentConfig())
    parent.conv.add_user("父消息")
    res = await launcher.launch_fork("fork-task")
    assert res.status == "async_launched"
    await asyncio.sleep(0.05)
    conv = mgr.get(res.task_id).sub_agent.conv.get_context()
    assert conv[0].content == "父消息"  # 继承父历史
    assert any(m.role == "user" and "<fork_boilerplate>" in m.content for m in conv)


async def test_fork_blocked_when_background_disabled():
    launcher, _parent, _ = _make(
        SeqProvider([_txt()]), cfg=AgentConfig(enable_subagent_background=False)
    )
    res = await launcher.launch_fork("t")
    assert "后台禁用，无法 Fork" in res.error


async def test_max_turns_resolution_global_default():
    """防的 bug：角色未设 maxTurns 时被硬编码 10 卡住——应回落 agents.max_turns 全局默认；
    角色显式设了 → 角色值优先；fork 跟随全局。"""
    reg = Registry.default()
    parent = Agent(
        SeqProvider([_txt()]),
        ConversationManager(20),
        reg,
        "s",
        "e",
        permission=PermissionChecker.create(tempfile.mkdtemp()),
    )
    launcher = SubAgentLauncher(
        SeqProvider([_txt()]),
        None,
        parent.permission,
        None,
        StubCatalog(),
        TaskManager(),
        AgentConfig(max_turns=25),
        lambda: parent,
    )
    role_unset = AgentDefinition(
        name="u", description="d", body="b", max_turns=0, source=Source.BUILTIN
    )
    sub, _ = launcher.make_sub_agent(role_unset, is_background=False)
    assert sub._max_turns == 25  # 全局默认
    role_set = AgentDefinition(
        name="s", description="d", body="b", max_turns=30, source=Source.BUILTIN
    )
    sub2, _ = launcher.make_sub_agent(role_set, is_background=False)
    assert sub2._max_turns == 30  # 角色显式优先


async def test_fork_uses_global_max_turns():
    mgr = TaskManager()
    launcher, _parent, mgr = _make(SeqProvider([_txt()]), cfg=AgentConfig(max_turns=18))
    res = await launcher.launch_fork("t")
    await asyncio.sleep(0.05)
    assert mgr.get(res.task_id).sub_agent._max_turns == 18  # fork 跟随全局


async def test_model_tiers_resolution():
    made = []

    def mp(model):
        made.append(model)
        return SeqProvider([_txt("m")])

    reg = Registry.default()
    parent = Agent(
        SeqProvider([_txt()]),
        ConversationManager(20),
        reg,
        "s",
        "e",
        permission=PermissionChecker.create(tempfile.mkdtemp()),
    )
    launcher = SubAgentLauncher(
        SeqProvider([_txt()]),
        mp,
        parent.permission,
        None,
        StubCatalog(),
        TaskManager(),
        AgentConfig(model_tiers={"haiku": "h-model"}),
        lambda: parent,
    )
    launcher.resolve_model("haiku")
    assert made == ["h-model"]
    # 缺配置 tier → 降级父（不调 make_provider）
    made.clear()
    launcher.resolve_model("opus")
    assert made == []
