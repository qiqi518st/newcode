"""LoadSkill 系统级只读工具（F4.2/N5/F3.5）。

Agent 判断用户意图匹配某个 Skill 时调用，做三件事：
1. 把 SKILL.md 完整 prompt body 激活进 Agent 环境上下文（store.activate，不塞普通消息历史）。
2. 目录型 Skill 的 tool.json 声明工具注册进当前会话。
3. 返回简短确认（不返回完整 SOP，避免 tool_result 占用上下文空间，F4.2.3）。

read_only=True（READONLY 类不弹权限提示，N5）、is_system=True（豁免 allowedTools，F3.5，
支持 Skill 嵌套触发——一个 Skill 的 SOP 里可再调 load_skill 激活另一个）。
"""

from __future__ import annotations

from ..provider.base import ToolResult
from ..skills.catalog import register_skill_tools


class LoadSkillTool:
    """LoadSkill 工具实现（系统工具，恒可见）。"""

    def __init__(self, catalog, active, registry) -> None:
        self._catalog = catalog
        self._active = active
        self._registry = registry

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def description(self) -> str:
        return (
            "激活一个 Skill 到当前会话：加载其完整指令到环境上下文，并注册其专属工具。"
            "当用户请求匹配某个可用 Skill 时调用（可用列表见环境中的 Available Skills 段）。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要激活的 Skill 名字",
                },
            },
            "required": ["name"],
        }

    @property
    def read_only(self) -> bool:
        return True  # 只读类别 → READONLY 权限，不弹权限提示（N5）

    @property
    def is_system(self) -> bool:
        return True  # 系统工具 → 豁免 allowedTools 过滤（F3.5）

    async def execute(self, arguments: dict) -> ToolResult:
        """激活 Skill：重读 body → 目录型注册工具 → store.activate → 简短确认。"""
        name = str(arguments.get("name", "")).strip()
        if not name:
            return ToolResult(
                status="error",
                error="load_skill: missing required argument 'name'",
            )
        skill = self._catalog.get(name)
        if skill is None:
            available = ", ".join(self._catalog.names())
            return ToolResult(
                status="error",
                error=f"unknown skill: {name}"
                + (f" (available: {available})" if available else ""),
            )
        register_skill_tools(self._registry, skill)
        self._active.activate(skill.name, skill.prompt_body)
        return ToolResult(
            status="ok",
            output=f"Skill {skill.name} activated. SOP pinned to environment context.",
        )
