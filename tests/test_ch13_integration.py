"""ch13 端到端集成测试（mock provider 驱动真实代码路径）。

防的 bug（CLAUDE.md「重复路径交叉验证」）：
- 主 Agent 经真实 ReAct 循环调 Agent 工具 → 子 Agent 结果未回到主对话
- Fork 路径未强制后台 / 未继承父历史
- 嵌套防护失效（子 Agent 工具集出现 agent 工具）
- 续派变新任务（同 id 复用失败）
- 完成通知未注入主对话历史
"""

from __future__ import annotations

import asyncio
import json

import pytest

from newcode.agent import Agent, EventType
from newcode.conversation.manager import ConversationManager
from newcode.permission.checker import PermissionChecker
from newcode.permission.modes import PermissionMode
from newcode.provider.base import StreamEvent, TokenUsage, ToolCall
from newcode.subagent.catalog import load_catalog
from newcode.subagent.config import AgentConfig
from newcode.subagent.launcher import SubAgentLauncher
from newcode.subagent.manager import Status, TaskManager
from newcode.tools.agent_tool import AgentTool
from newcode.tools.registry import Registry

pytestmark = pytest.mark.anyio


class ScriptProvider:
    """按调用序号返回脚本：call0=agent 工具调用（主）、call1=子 Agent 文本、call2=主终稿。"""

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


def _assemble(catalog, manager, provider):
    """复刻 main.py 装配的核心：registry（含 agent 工具）+ launcher + 主 Agent。

    主 Agent 用 BYPASS 权限：agent 工具是 COMMAND 类，default 下 ASK 会弹 HITL
    阻塞 mock 测试——测试环境直接放行（真实使用由用户经 TUI 批准）。
    """
    registry = Registry.default()
    perm = PermissionChecker.create("/tmp")
    perm.set_mode(PermissionMode.BYPASS)
    parent = Agent(
        provider,
        ConversationManager(20),
        registry,
        "stable",
        "env",
        permission=perm,
    )
    launcher = SubAgentLauncher(
        provider,
        None,
        parent.permission,
        None,
        catalog,
        manager,
        AgentConfig(async_timeout_s=0.2),
        lambda: parent,
    )
    registry.register(AgentTool(catalog, launcher, lambda: parent))
    return parent, launcher, registry


async def test_defined_foreground_end_to_end():
    """主 Agent 调 agent(subagent_type=explore) → 子 Agent 前台执行 → 结果回到主对话。"""
    mgr = TaskManager()
    catalog = load_catalog("/tmp", AgentConfig())
    provider = ScriptProvider(
        [
            _tool("agent", {"prompt": "查一下", "subagent_type": "explore"}),
            _txt("sub-result"),
            _txt("main-final"),
        ]
    )
    parent, launcher, _ = _assemble(catalog, mgr, provider)

    async for event in parent.run("帮我查一下函数定义", mode="normal"):
        if event.type == EventType.DONE:
            break
    # 主对话历史应含子 Agent 结果（tool result 消息）
    texts = [m.content for m in parent.conv.get_context()]
    assert any("sub-result" in t for t in texts), texts
    # 嵌套防护：子 Agent 工具集无 agent（经 launcher 直接断言）
    role = catalog.resolve("explore")
    sub_names = launcher.build_sub_registry(role, is_background=False).names()
    assert "agent" not in sub_names


async def test_fork_background_notification_and_continue():
    """Fork 强制后台 + 继承父历史 + 完成通知 + 同 id 续派。"""
    mgr = TaskManager(max_tasks_per_agent=3)
    catalog = load_catalog("/tmp", AgentConfig())
    provider = ScriptProvider([_txt("sub")])
    parent, launcher, _ = _assemble(catalog, mgr, provider)
    parent.conv.add_user("父消息")

    res = await launcher.launch_fork("fork-task", name="worker-1")
    assert res.status == "async_launched"  # 强制后台
    await asyncio.sleep(0.05)
    bt = mgr.get(res.task_id)
    assert bt is not None and bt.status == Status.COMPLETED
    # 继承父历史 + Boilerplate
    conv = bt.sub_agent.conv.get_context()
    assert conv[0].content == "父消息"
    assert any("<fork_boilerplate>" in m.content for m in conv if m.role == "user")
    # 完成通知可经 TUI drain 注入（模拟：build_task_notification 内容）
    from newcode.subagent.manager import build_task_notification

    xml = build_task_notification(bt)
    assert "<task-notification>" in xml and "completed" in xml

    # 同 id 续派
    assert mgr.continue_agent("worker-1", "接着做") == res.task_id
    await asyncio.sleep(0.05)
    assert mgr.get(res.task_id).round == 2
    assert mgr.get(res.task_id).result == "sub"


async def test_agent_tool_via_main_loop_fork_path():
    """主 Agent 不带 subagent_type 调 agent 工具 → Fork 后台，返回 {task_id, async_launched}。"""
    mgr = TaskManager()
    catalog = load_catalog("/tmp", AgentConfig())
    provider = ScriptProvider(
        [_tool("agent", {"prompt": "fork 我"}), _txt("main-final")]
    )
    parent, _, _ = _assemble(catalog, mgr, provider)

    out = ""
    async for event in parent.run("继续这个任务", mode="normal"):
        if (
            event.type == EventType.TOOL_RESULT
            and event.payload.status == "ok"
            and "async_launched" in event.payload.output
        ):
            out = event.payload.output
        if event.type == EventType.DONE:
            break
    data = json.loads(out)
    assert data["status"] == "async_launched"
    assert data["task_id"].startswith("agent-")


async def test_nesting_guard_all_layers():
    """嵌套防护：子 Agent 工具集无 agent；Fork 子 Agent 调 agent 工具被标记检查拦截。"""
    from newcode.subagent.fork import FORK_BOILERPLATE

    mgr = TaskManager()
    catalog = load_catalog("/tmp", AgentConfig())
    provider = ScriptProvider([_txt("x")])
    parent, launcher, _ = _assemble(catalog, mgr, provider)

    # 层 1（F6.1/F6.3）：定义式/后台子 Agent 工具集均无 agent
    role = catalog.resolve("explore")
    for bg in (False, True):
        assert (
            "agent" not in launcher.build_sub_registry(role, is_background=bg).names()
        )

    # 层 2（B2 层 1）：主 conv 含 fork 标记 → AgentTool 拒绝
    parent.conv.add_user(FORK_BOILERPLATE + "t")
    from newcode.tools.agent_tool import AgentTool as AT

    tool = AT(catalog, launcher, lambda: parent)
    r = await tool.execute({"prompt": "x", "subagent_type": "explore"})
    assert r.status == "error" and "Fork 子 Agent 不能再启动 Agent" in r.error


async def test_clear_all_ends_background():
    mgr = TaskManager()
    catalog = load_catalog("/tmp", AgentConfig())
    provider = ScriptProvider([_txt("x")])
    _, launcher, _ = _assemble(catalog, mgr, provider)
    res = await launcher.launch_fork("t")
    mgr.clear_all()
    assert mgr.get(res.task_id) is None
