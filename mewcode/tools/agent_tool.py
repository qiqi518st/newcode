"""Agent 工具（ch13 F1）：主 Agent 的统一子 Agent 入口。

- 参数 schema 不随角色变化（F1.4）；ch14 增 isolation 参数（动态隔离覆盖，不破坏稳定性）
- description 渲染 catalog 角色列表，帮主 LLM 选择 subagent_type（F1.2）
- 防嵌套兜底（B2 层 1）：主 conv 含 <fork_boilerplate> 标记 → 直接拒绝（F6.5）
- execute 转发 launcher（定义式 / Fork 式），返回同步文本或 {task_id, status}
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..provider.base import ToolResult
from .agent_worktree import _execute_with_worktree
from .base import Tool

if TYPE_CHECKING:
    from ..subagent.catalog import Catalog
    from ..subagent.launcher import SubAgentLauncher
    from ..worktree import Manager as WorktreeManager
    from ..worktree.config import WorktreesConfig

_MODEL_ENUM = ["haiku", "sonnet", "opus", "inherit"]


class AgentTool(Tool):
    """主 Agent 调用的 Agent 工具（内部名 `agent`，spec F1）。"""

    def __init__(
        self,
        catalog: Catalog,
        launcher: SubAgentLauncher,
        get_main_agent: Callable[[], object],
        worktree_mgr: WorktreeManager | None = None,
        worktrees_cfg: WorktreesConfig | None = None,
    ) -> None:
        self._catalog = catalog
        self._launcher = launcher
        self._get_main_agent = get_main_agent
        # ch14：worktree 管理器与配置（None / enable=false → 隔离降级不启用，F11.2）
        self._worktree_mgr = worktree_mgr
        self._worktrees_cfg = worktrees_cfg
        names = ", ".join(d.name for d in catalog.list())
        self._description = (
            "启动一个子 Agent 执行独立任务（独立上下文，不污染主对话）。"
            "隔离：可传 isolation='worktree' 让本次子 Agent 在独立 Git Worktree 中运行"
            "（或用 frontmatter 声明 isolation:worktree 的角色），isolation='none' 强制不隔离。"
            + (
                f" 可用 subagent_type: {names}"
                if names
                else " 不指定 subagent_type 走 Fork 路径。"
            )
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
                "isolation": {
                    "type": "string",
                    "enum": ["worktree", "none"],
                    "description": (
                        "本次调用动态覆盖隔离：worktree=强制在独立 Git Worktree 中运行；"
                        "none=强制不隔离；不传沿用角色 frontmatter 的 isolation 声明"
                    ),
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
        # ch14 F9：动态 isolation 覆盖（worktree/none/空=沿用角色声明；非法值回落空）
        isolation_arg = str(arguments.get("isolation") or "").strip().lower()
        if isolation_arg not in ("", "worktree", "none"):
            isolation_arg = ""

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
            role = self._catalog.resolve(subagent_type)
            # ch14 F8.2/F9：动态 isolation 参数 > 角色 frontmatter 声明（生效值取两者并集）
            eff_isolation = isolation_arg or (role.isolation if role else "")
            if eff_isolation == "worktree" and role is not None:
                if self._worktree_mgr is not None and self._worktree_mgr.cfg.enable:
                    # 隔离可用 → worktree 分支（强制前台，F8.4）；
                    # 子 Agent 在 _execute_with_worktree 内于 worktree 创建后构造
                    result = await _execute_with_worktree(
                        self._worktree_mgr,
                        self._launcher,
                        role,
                        prompt,
                        model_override=model,
                    )
                elif isolation_arg:
                    # 动态显式请求隔离但不可用 → 结构化错误（不静默降级，N6）
                    return ToolResult(
                        status="error",
                        error="worktree 隔离不可用（worktrees 未启用或非 git 仓库）",
                    )
                else:
                    # 角色静态声明但 worktree 不可用 → F11.2 降级为不隔离
                    result = await self._launcher.launch_defined(
                        subagent_type,
                        prompt,
                        name=name,
                        background=run_bg,
                        model_override=model,
                    )
            else:
                result = await self._launcher.launch_defined(
                    subagent_type,
                    prompt,
                    name=name,
                    background=run_bg,
                    model_override=model,
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
