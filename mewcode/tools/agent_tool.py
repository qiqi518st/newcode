"""Agent 工具（ch13 F1）：主 Agent 的统一子 Agent 入口。

- 参数 schema 固定（prompt/description/subagent_type/model/run_in_background/name），
  不随角色变化（F1.4）
- description 渲染 catalog 角色列表，帮主 LLM 选择 subagent_type（F1.2）
- 防嵌套兜底（B2 层 1）：主 conv 含 <fork_boilerplate> 标记 → 直接拒绝（F6.5）
- execute 转发 launcher（定义式 / Fork 式），返回同步文本或 {task_id, status}
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..provider.base import ToolResult
from .base import Tool

if TYPE_CHECKING:
    from ..subagent.catalog import Catalog
    from ..subagent.launcher import SubAgentLauncher

_MODEL_ENUM = ["haiku", "sonnet", "opus", "inherit"]


class AgentTool(Tool):
    """主 Agent 调用的 Agent 工具（内部名 `agent`，spec F1）。"""

    def __init__(
        self,
        catalog: Catalog,
        launcher: SubAgentLauncher,
        get_main_agent: Callable[[], object],
    ) -> None:
        self._catalog = catalog
        self._launcher = launcher
        self._get_main_agent = get_main_agent
        names = ", ".join(d.name for d in catalog.list())
        self._description = (
            "启动一个子 Agent 执行独立任务（独立上下文，不污染主对话）。"
            + (f" 可用 subagent_type: {names}" if names else " 不指定 subagent_type 走 Fork 路径。")
        )

    @property
    def name(self) -> str:
        return "agent"

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "交给子 Agent 的任务指令（必填）",
                },
                "description": {
                    "type": "string",
                    "description": "一句话描述任务，供 UI 展示",
                },
                "subagent_type": {
                    "type": "string",
                    "description": "预定义角色名；留空走 Fork 路径（继承父对话历史）",
                },
                "model": {
                    "type": "string",
                    "enum": _MODEL_ENUM,
                    "description": "模型覆盖：haiku/sonnet/opus/inherit；留空沿用角色 model",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "true 强制后台执行（立即返回 task_id）；缺省前台，超时自动转后台",
                },
                "name": {
                    "type": "string",
                    "description": "给子 Agent 命名，供 SendMessage 续派寻址",
                },
            },
            "required": ["prompt"],
        }

    @property
    def read_only(self) -> bool:
        return False  # 子 Agent 可能做任何事（F1）

    @property
    def is_system(self) -> bool:
        return False  # 非系统工具：正常参与子 Agent 过滤（全局禁止剔除，F6.1）

    async def execute(self, arguments: dict) -> ToolResult:
        prompt = str(arguments.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(status="error", error="prompt 必填")
        subagent_type = str(arguments.get("subagent_type") or "").strip()
        name = str(arguments.get("name") or "").strip() or None
        run_bg = bool(arguments.get("run_in_background") or False)
        model = str(arguments.get("model") or "").strip()

        # 防嵌套兜底（B2 层 1，F6.5）：主对话含 fork 标记 → 拒绝（正常情况下子 Agent
        # 工具集已被过滤剔除 agent，此步是双保险）
        parent = self._get_main_agent()
        parent_conv = getattr(parent, "conv", None)
        if parent_conv is not None:
            from ..subagent.fork import is_fork_context

            if is_fork_context(parent_conv.get_context()):
                return ToolResult(
                    status="error", error="Fork 子 Agent 不能再启动 Agent"
                )

        if subagent_type:
            result = await self._launcher.launch_defined(
                subagent_type, prompt, name=name, background=run_bg, model_override=model
            )
        else:
            result = await self._launcher.launch_fork(prompt, name=name)

        if result.error:
            return ToolResult(status="error", error=result.error)
        if result.status == "completed":
            return ToolResult(status="ok", output=result.text)
        # async_launched / timed_out_to_background：返回 {task_id, status}
        return ToolResult(
            status="ok",
            output=json.dumps(
                {"task_id": result.task_id, "status": result.status},
                ensure_ascii=False,
            ),
        )
