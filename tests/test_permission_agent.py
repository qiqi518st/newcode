"""Agent + 权限系统集成测试（T13）

背景：ch06 把权限检查接进 Agent Loop（agent.py 的 known_calls 权限裁决段）。
这些测试防的 bug：
- 权限 Deny 回灌后 Loop 中断或死循环
- 单批多个工具调用时结果与调用串位（工具结果回灌错 ID）
- HITL 阻塞后 resolve_hitl 无法唤醒 / cancel 死锁
- 永久放行不写本地规则文件
- plan 模式工具集被硬性过滤
"""

from typing import ClassVar

import pytest

from newcode.agent import Agent, EventType, StopReason
from newcode.conversation.manager import ConversationManager
from newcode.permission.checker import PermissionChecker
from newcode.permission.hitl import HITLResponse
from newcode.permission.modes import PermissionMode
from newcode.permission.rules import Rule, RuleLayers
from newcode.provider.base import (
    StreamEvent,
    TokenUsage,
    ToolCall,
    ToolResult,
)
from newcode.tools.registry import Registry


class MockWriteTool:
    """模拟写文件工具（不落盘，返回固定结果）"""

    name: ClassVar[str] = "write_file"
    description: ClassVar[str] = "mock write"
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}
    read_only: ClassVar[bool] = False

    async def execute(self, arguments):
        return ToolResult(status="ok", output="wrote:" + str(arguments.get("path")))


class MockReadTool:
    """模拟只读工具"""

    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = "mock read"
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}
    read_only: ClassVar[bool] = True

    async def execute(self, arguments):
        return ToolResult(status="ok", output="read:" + str(arguments.get("path")))


class ScriptedProvider:
    """按剧本产出各轮事件：str→文本；list[ToolCall]→一批工具调用"""

    def __init__(self, turns) -> None:
        self._turns = list(turns)
        self._i = 0
        self.last_payload = None
        self._name = "mock"
        self._model = "mock-model"

    @property
    def name(self):
        return self._name

    @property
    def model(self):
        return self._model

    async def stream(self, payload):
        self.last_payload = payload
        if self._i >= len(self._turns):
            yield StreamEvent(done=True, usage=TokenUsage(0, 0))
            return
        turn = self._turns[self._i]
        self._i += 1
        if isinstance(turn, str):
            yield StreamEvent(text=turn)
            yield StreamEvent(done=True, usage=TokenUsage(10, 5))
        else:
            for tc in turn:
                yield StreamEvent(tool_call=tc)
            yield StreamEvent(done=True, usage=TokenUsage(10, 5))


def _agent(
    provider,
    registry,
    root,
    mode=PermissionMode.DEFAULT,
    layers=None,
    interactive=True,
):
    """构造带权限检查器的 Agent"""
    checker = PermissionChecker(
        project_root=str(root), mode=mode, layers=layers or RuleLayers()
    )
    conv = ConversationManager(20)
    agent = Agent(
        provider,
        conv,
        registry,
        "mock-stable",
        "mock-env",
        permission=checker,
        is_interactive=interactive,
    )
    return agent, checker


@pytest.mark.anyio
async def test_deny_fed_back_loop_continues(tmp_path):
    """Deny 回灌不中断：被拒结果 isError=True，Loop 继续到次轮"""
    registry = Registry()
    registry.register(MockWriteTool())
    layers = RuleLayers()
    layers.project.deny.append(Rule("Write", "**", "deny", "project"))
    provider = ScriptedProvider(
        [
            [ToolCall("write_file", {"path": "a.txt"}, tool_use_id="w1")],
            "adjusted",
        ]
    )
    agent, _ = _agent(provider, registry, tmp_path, layers=layers)
    events = []
    async for e in agent.run("write a file"):
        events.append(e)

    types = [e.type for e in events]
    assert EventType.HITL_REQUEST not in types  # deny 不弹 HITL
    # 被拒结果以 error 回灌模型
    results = [e.payload for e in events if e.type == EventType.TOOL_RESULT]
    assert results and results[0].status == "error"
    assert "deny" in results[0].error.lower() or "拒绝" in results[0].error
    # Loop 继续到次轮并自然终止
    done = events[-1]
    assert done.type == EventType.DONE
    assert done.payload == StopReason.NATURAL
    # 次轮的文本出现，证明没有中断
    assert any(e.type == EventType.TEXT and e.payload == "adjusted" for e in events)


@pytest.mark.anyio
async def test_batch_pairing_no_misalignment(tmp_path):
    """保序配对回灌：单批「被拒+放行」结果各配其 ID，不串位"""
    registry = Registry()
    registry.register(MockWriteTool())
    registry.register(MockReadTool())
    layers = RuleLayers()
    layers.project.deny.append(Rule("Write", "a.txt", "deny", "project"))
    provider = ScriptedProvider(
        [
            [
                ToolCall("write_file", {"path": "a.txt"}, tool_use_id="w1"),
                ToolCall("read_file", {"path": "b.py"}, tool_use_id="r1"),
            ],
            "done",
        ]
    )
    agent, _ = _agent(provider, registry, tmp_path, layers=layers)
    events = []
    async for e in agent.run("do both"):
        events.append(e)

    # 收集 (tool_use_id, status, content) 配对
    pairs = {}
    last_call = None
    for e in events:
        if e.type == EventType.TOOL_CALL:
            last_call = e.payload
        elif e.type == EventType.TOOL_RESULT:
            pairs[last_call.tool_use_id] = (
                last_call.tool_name,
                e.payload.status,
                e.payload.output or e.payload.error,
            )

    assert pairs["w1"] == ("write_file", "error", "匹配 deny 规则：Write(a.txt)")
    assert pairs["r1"][0] == "read_file"
    assert pairs["r1"][1] == "ok"
    assert pairs["r1"][2] == "read:b.py"

    # 历史中两条 tool 消息配对正确，未串位
    tool_msgs = [m for m in agent.conv.get_context() if m.role == "tool"]
    assert len(tool_msgs) == 2
    ids = sorted(m.tool_use_id for m in tool_msgs)
    assert ids == ["r1", "w1"]


@pytest.mark.anyio
async def test_ask_hitl_allow_once_executes(tmp_path):
    """Ask 人在回路：收 HITL_REQUEST → resolve(allow_once) → 工具执行"""
    registry = Registry()
    registry.register(MockWriteTool())
    provider = ScriptedProvider(
        [
            [ToolCall("write_file", {"path": "a.txt"}, tool_use_id="w1")],
            "done",
        ]
    )
    agent, _ = _agent(provider, registry, tmp_path)
    events = []
    async for e in agent.run("write"):
        if e.type == EventType.HITL_REQUEST:
            agent.resolve_hitl(HITLResponse(action="allow_once"))
        events.append(e)

    hitl_seen = [e for e in events if e.type == EventType.HITL_REQUEST]
    assert hitl_seen, "应发出 HITL_REQUEST"
    # allow_once 后工具执行成功
    results = [e.payload for e in events if e.type == EventType.TOOL_RESULT]
    assert results and results[0].status == "ok"
    assert results[0].output == "wrote:a.txt"
    assert events[-1].payload == StopReason.NATURAL


@pytest.mark.anyio
async def test_ask_hitl_deny_fed_back(tmp_path):
    """Ask 人在回路：resolve(deny) → 工具不执行，拒绝原因回灌"""
    registry = Registry()
    registry.register(MockWriteTool())
    provider = ScriptedProvider(
        [
            [ToolCall("write_file", {"path": "a.txt"}, tool_use_id="w1")],
            "done",
        ]
    )
    agent, _ = _agent(provider, registry, tmp_path)
    events = []
    async for e in agent.run("write"):
        if e.type == EventType.HITL_REQUEST:
            agent.resolve_hitl(HITLResponse(action="deny"))
        events.append(e)

    results = [e.payload for e in events if e.type == EventType.TOOL_RESULT]
    assert results and results[0].status == "error"
    assert "拒绝" in results[0].error
    assert events[-1].payload == StopReason.NATURAL


@pytest.mark.anyio
async def test_allow_always_writes_local_file(tmp_path):
    """永久放行：resolve(allow_always) → 本地规则文件写入 allow 条目"""
    registry = Registry()
    registry.register(MockWriteTool())
    provider = ScriptedProvider(
        [
            [ToolCall("write_file", {"path": "a.txt"}, tool_use_id="w1")],
            "done",
        ]
    )
    agent, _ = _agent(provider, registry, tmp_path)
    events = []
    async for e in agent.run("write"):
        if e.type == EventType.HITL_REQUEST:
            agent.resolve_hitl(HITLResponse(action="allow_always"))
        events.append(e)

    local = tmp_path / ".newcode" / "permissions.local.yaml"
    assert local.exists(), "应写入本地级规则文件"
    content = local.read_text(encoding="utf-8")
    assert "Write(a.txt)" in content


@pytest.mark.anyio
async def test_readonly_batch_no_hitl_and_concurrent(tmp_path):
    """只读并发不退化：一批只读不产生任何 HITL_REQUEST，全部执行"""
    registry = Registry()
    registry.register(MockReadTool())
    provider = ScriptedProvider(
        [
            [
                ToolCall("read_file", {"path": "a.py"}, tool_use_id="r1"),
                ToolCall("read_file", {"path": "b.py"}, tool_use_id="r2"),
                ToolCall("read_file", {"path": "c.py"}, tool_use_id="r3"),
            ],
            "done",
        ]
    )
    agent, _ = _agent(provider, registry, tmp_path)
    events = []
    async for e in agent.run("read all"):
        events.append(e)

    assert not any(e.type == EventType.HITL_REQUEST for e in events)
    results = [e.payload for e in events if e.type == EventType.TOOL_RESULT]
    assert len(results) == 3
    assert all(r.status == "ok" for r in results)


@pytest.mark.anyio
async def test_hitl_cancel_unblocks_cleanly(tmp_path):
    """取消：HITL 等待中 cancel() → Loop 干净收尾、历史合法"""
    registry = Registry()
    registry.register(MockWriteTool())
    provider = ScriptedProvider(
        [
            [ToolCall("write_file", {"path": "a.txt"}, tool_use_id="w1")],
        ]
    )
    agent, _ = _agent(provider, registry, tmp_path)
    events = []
    async for e in agent.run("write"):
        if e.type == EventType.HITL_REQUEST:
            agent.cancel()  # 兜底解阻塞
        events.append(e)

    assert events[-1].type == EventType.DONE
    assert events[-1].payload == StopReason.CANCELLED
    # 历史合法：user + assistant(tool_calls) + tool(被取消原因)
    msgs = agent.conv.get_context()
    roles = [m.role for m in msgs]
    assert roles == ["user", "assistant", "tool"]


@pytest.mark.anyio
async def test_non_interactive_ask_becomes_deny(tmp_path):
    """非交互 -c：需确认操作默认拒绝并回灌（不弹 HITL）"""
    registry = Registry()
    registry.register(MockWriteTool())
    provider = ScriptedProvider(
        [
            [ToolCall("write_file", {"path": "a.txt"}, tool_use_id="w1")],
            "adjusted",
        ]
    )
    agent, _ = _agent(provider, registry, tmp_path, interactive=False)
    events = []
    async for e in agent.run("write"):
        events.append(e)

    assert not any(e.type == EventType.HITL_REQUEST for e in events)
    results = [e.payload for e in events if e.type == EventType.TOOL_RESULT]
    assert results and results[0].status == "error"
    assert events[-1].payload == StopReason.NATURAL


@pytest.mark.anyio
async def test_plan_mode_exposes_all_tools(tmp_path):
    """plan 迁移：PLAN 模式仍暴露全部工具定义（含写工具）"""
    registry = Registry()
    registry.register(MockReadTool())
    registry.register(MockWriteTool())
    provider = ScriptedProvider(["plan result"])
    agent, _ = _agent(provider, registry, tmp_path)
    payloads = []
    async for e in agent.run("analyze", mode="plan"):
        pass
    payloads.append(provider.last_payload)

    assert payloads and payloads[0].tools is not None
    names = {t.name for t in payloads[0].tools}
    # 写工具也在其中，不硬性过滤
    assert "write_file" in names
    assert "read_file" in names
    # plan 模式注入计划提醒
    assert payloads[0].reminders
