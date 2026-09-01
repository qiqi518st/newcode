"""后台任务工具组（ch13 F8.1）：TaskList / TaskGet / TaskStop / SendMessage。

- 内部 snake_case 命名（task_list/task_get/task_stop/send_message），与既有工具一致
- **不设 is_system**：子 Agent 经过滤看不到管理工具（F6.3，防元工具泄漏）
- SendMessage 支持 task_id 或 name 寻址（F7.10，同 id 续派）
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..provider.base import ToolResult
from .base import Tool

if TYPE_CHECKING:
    from ..subagent.manager import TaskManager


def _summary(task: object) -> dict:
    return {
        "id": task.id,
        "name": task.name,
        "status": task.status.name.lower(),
        "round": task.round,
        "tool_count": task.tool_count,
        "last_activity": task.last_activity,
    }


def _full(task: object) -> dict:
    return {
        **_summary(task),
        "role": task.role,
        "result": task.result,
        "error": str(task.err) if task.err else "",
        "start_time": task.start_time,
        "end_time": task.end_time,
        "usage_in": task.usage.input_tokens,
        "usage_out": task.usage.output_tokens,
        "total_usage_in": task.total_usage.input_tokens,
        "total_usage_out": task.total_usage.output_tokens,
    }


class TaskListTool(Tool):
    """TaskList：列出后台任务摘要（无参，F8.1）。"""

    def __init__(self, manager: TaskManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "task_list"

    @property
    def description(self) -> str:
        return "列出当前所有后台子 Agent 任务（id/name/status/round/tool_count/last_activity）"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def read_only(self) -> bool:
        return True

    @property
    def is_system(self) -> bool:
        return False

    async def execute(self, arguments: dict) -> ToolResult:
        items = [_summary(t) for t in self._manager.list()]
        return ToolResult(status="ok", output=json.dumps(items, ensure_ascii=False))


class TaskGetTool(Tool):
    """TaskGet：按 task_id 返回完整状态（含 result/error/usage/round，F8.1）。"""

    def __init__(self, manager: TaskManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "task_get"

    @property
    def description(self) -> str:
        return "按 task_id 获取后台子 Agent 任务的完整状态"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "任务 ID"}},
            "required": ["task_id"],
        }

    @property
    def read_only(self) -> bool:
        return True

    @property
    def is_system(self) -> bool:
        return False

    async def execute(self, arguments: dict) -> ToolResult:
        task_id = str(arguments.get("task_id") or "").strip()
        task = self._manager.get(task_id)
        if task is None:
            return ToolResult(status="error", error=f"task not found: {task_id}")
        return ToolResult(
            status="ok", output=json.dumps(_full(task), ensure_ascii=False)
        )


class TaskStopTool(Tool):
    """TaskStop：终止运行中任务（F8.1）。"""

    def __init__(self, manager: TaskManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "task_stop"

    @property
    def description(self) -> str:
        return "终止一个运行中的后台子 Agent 任务"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "任务 ID"}},
            "required": ["task_id"],
        }

    @property
    def read_only(self) -> bool:
        return False

    @property
    def is_system(self) -> bool:
        return False

    async def execute(self, arguments: dict) -> ToolResult:
        task_id = str(arguments.get("task_id") or "").strip()
        if not self._manager.stop(task_id):
            return ToolResult(status="error", error=f"task not found: {task_id}")
        return ToolResult(
            status="ok", output=json.dumps({"status": "cancellation_requested"})
        )


class SendMessageTool(Tool):
    """SendMessage：给仍存活的后台子 Agent 续派任务（同 id 复用，F7.10/F8.1）。"""

    def __init__(self, manager: TaskManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "send_message"

    @property
    def description(self) -> str:
        return "给一个已完成的后台子 Agent 追加新任务继续跑（task_id 或 name 寻址）"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID（与 name 二选一）"},
                "name": {"type": "string", "description": "spawn 时的名字（与 task_id 二选一）"},
                "message": {"type": "string", "description": "续派任务文本"},
            },
            "required": ["message"],
        }

    @property
    def read_only(self) -> bool:
        return False

    @property
    def is_system(self) -> bool:
        return False

    async def execute(self, arguments: dict) -> ToolResult:
        task_id = str(arguments.get("task_id") or "").strip()
        name = str(arguments.get("name") or "").strip()
        message = str(arguments.get("message") or "").strip()
        target = task_id or name
        if not target:
            return ToolResult(status="error", error="task_id 或 name 至少一个")
        if not message:
            return ToolResult(status="error", error="message 必填")
        try:
            tid = self._manager.continue_agent(target, message)
        except Exception as exc:  # noqa: BLE001 —— TaskNotFound/TaskBusy/TaskCapReached → 结构化错误
            return ToolResult(status="error", error=str(exc))
        return ToolResult(
            status="ok",
            output=json.dumps({"status": "accepted", "task_id": tid}),
        )
