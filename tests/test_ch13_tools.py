"""ch13 工具（AgentTool + Task 工具组）测试。

防的 bug：
- Agent 工具参数 schema 随角色变化（F1.4：必须固定）
- description 不渲染角色 → 主 LLM 不知道可用 subagent_type
- Fork 嵌套兜底失效（主 conv 含 <fork_boilerplate> 时必须拒绝）
- SendMessage 找不到目标静默成功（必须结构化错误）
- Task 工具设 is_system=True → 子 Agent 豁免看到管理工具（F6.3）
"""

from __future__ import annotations

import asyncio
import json

import pytest

from newcode.conversation.manager import ConversationManager
from newcode.subagent.fork import FORK_BOILERPLATE
from newcode.subagent.launcher import LaunchResult
from newcode.subagent.manager import Status, TaskManager
from newcode.subagent.types import AgentDefinition, Source
from newcode.tools.agent_tool import AgentTool
from newcode.tools.task_tools import (
    SendMessageTool,
    TaskGetTool,
    TaskListTool,
    TaskStopTool,
)

pytestmark = pytest.mark.anyio


class StubCatalog:
    def __init__(self):
        self.roles = {
            "explore": AgentDefinition(
                name="explore", description="d", body="b", source=Source.BUILTIN
            )
        }
        self.listed = [
            AgentDefinition(
                name="explore", description="d", body="b", source=Source.BUILTIN
            )
        ]

    def resolve(self, name):
        return self.roles.get(name)

    def list(self):
        return self.listed

    def fork_definition(self):
        return AgentDefinition(
            name="__fork__", description="f", background=True, source=Source.BUILTIN
        )


class StubLauncher:
    def __init__(self):
        self.calls = []

    async def launch_defined(self, *a, **kw):
        self.calls.append(("defined", a, kw))
        return LaunchResult(status="completed", text="r:" + a[0])

    async def launch_fork(self, *a, **kw):
        self.calls.append(("fork", a, kw))
        return LaunchResult(task_id="agent-xyz", status="async_launched")


def _tool(launcher=None, parent_conv=None):
    cat = StubCatalog()
    parent = type("P", (), {"conv": parent_conv or ConversationManager(20)})()
    return AgentTool(cat, launcher or StubLauncher(), lambda: parent)


async def test_parameters_schema_stable():
    t = _tool()
    params = t.parameters
    assert params["required"] == ["prompt"]
    # 固定参数集（ch13 六参 + ch14 动态隔离 isolation + ch15 team_name/plan_mode_required）；
    # 不随角色增减而变化（F1.4）
    assert set(params["properties"]) == {
        "prompt",
        "description",
        "subagent_type",
        "model",
        "run_in_background",
        "name",
        "isolation",
        "team_name",
        "plan_mode_required",
    }
    assert t.read_only is False and t.is_system is False


def test_description_renders_roles():
    assert "explore" in _tool().description


async def test_missing_prompt_error():
    r = await _tool().execute({})
    assert r.status == "error" and "prompt" in r.error


async def test_defined_completion():
    t = _tool()
    r = await t.execute({"prompt": "查一下", "subagent_type": "explore"})
    assert r.status == "ok" and r.output == "r:explore"


async def test_unknown_role_error():
    l = StubLauncher()

    async def bad(*a, **kw):
        return LaunchResult(error="未知 subagent_type: nope")

    l.launch_defined = bad
    r = await _tool(l).execute({"prompt": "x", "subagent_type": "nope"})
    assert r.status == "error" and "未知" in r.error


async def test_fork_path_and_forwarding():
    l = StubLauncher()
    await _tool(l).execute(
        {
            "prompt": "x",
            "subagent_type": "explore",
            "run_in_background": True,
            "model": "haiku",
            "name": "w1",
        }
    )
    assert l.calls[0][0] == "defined"
    assert l.calls[0][2]["background"] is True
    assert l.calls[0][2]["model_override"] == "haiku"
    assert l.calls[0][2]["name"] == "w1"


async def test_fork_nesting_guard():
    conv = ConversationManager(20)
    conv.add_user(FORK_BOILERPLATE + "任务")
    r = await _tool(StubLauncher(), conv).execute(
        {"prompt": "x", "subagent_type": "explore"}
    )
    assert r.status == "error" and "Fork 子 Agent 不能再启动 Agent" in r.error


# ── Task 工具组 ────────────────────────────────────────


class FA:
    async def run_to_completion(self, task, **kw):
        return "done:" + task


async def test_task_tools_not_system():
    m = TaskManager()
    assert TaskListTool(m).is_system is False
    assert TaskGetTool(m).is_system is False
    assert TaskStopTool(m).is_system is False
    assert SendMessageTool(m).is_system is False


async def test_task_list_and_get():
    m = TaskManager()
    tid = m.launch(FA(), "t1", name="w1")
    await asyncio.sleep(0.05)
    items = json.loads((await TaskListTool(m).execute({})).output)
    assert items[0]["id"] == tid and items[0]["status"] == "completed"
    full = json.loads((await TaskGetTool(m).execute({"task_id": tid})).output)
    assert full["result"] == "done:t1" and full["round"] == 1
    assert (await TaskGetTool(m).execute({"task_id": "nope"})).status == "error"


async def test_task_stop():
    class H:
        async def run_to_completion(self, task, **kw):
            await asyncio.Event().wait()

    m = TaskManager()
    tid = m.launch(H(), "h")
    await asyncio.sleep(0.05)
    assert (
        "cancellation_requested"
        in (await TaskStopTool(m).execute({"task_id": tid})).output
    )
    await asyncio.sleep(0.05)
    assert m.get(tid).status == Status.CANCELLED


async def test_send_message_continue():
    m = TaskManager(max_tasks_per_agent=3)
    tid = m.launch(FA(), "r1")
    await asyncio.sleep(0.05)
    out = json.loads(
        (await SendMessageTool(m).execute({"task_id": tid, "message": "r2"})).output
    )
    assert out["task_id"] == tid
    await asyncio.sleep(0.05)
    assert m.get(tid).round == 2 and m.get(tid).result == "done:r2"


async def test_send_message_errors():
    m = TaskManager()
    assert (
        await SendMessageTool(m).execute({"task_id": "nope", "message": "x"})
    ).status == "error"
    assert (
        await SendMessageTool(m).execute({"message": "x"})
    ).status == "error"  # 缺 target
    assert (
        await SendMessageTool(m).execute({"task_id": "a"})
    ).status == "error"  # 缺 message
