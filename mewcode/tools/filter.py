"""工具过滤多层防线（ch13 F6）：常量 + 纯函数。

- GLOBAL_DENY（F6.1）：任何子 Agent 永不可用的工具（本期仅 agent 自身，防嵌套）
- ASYNC_AGENT_ALLOWED_TOOLS（F6.3）：后台工作者白名单，硬编码不受角色配置影响
- apply_agent_tool_filter（F6.4）：按顺序合并全局禁止 / 定义层黑名单 / 白名单 /
  后台交集 / 系统工具豁免
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import SYSTEM_TOOL_NAMES

# F6.1：任何子 Agent 永远不能用的工具名（防 A→B→C 链式嵌套，不可配置覆盖）
GLOBAL_DENY: frozenset[str] = frozenset({"agent"})

# ch15 TD-7：团队协作工具白名单——经 apply_agent_tool_filter 的普通子 Agent 天然不可见；
# 团队成员由 build_sub_registry(extra_tools=TEAMMATE_EXTRA_TOOLS) 在过滤后显式注入（N2）
TEAMMATE_EXTRA_TOOLS: frozenset[str] = frozenset(
    {
        "task_create",
        "task_get",
        "task_list",
        "task_update",
        "send_message",
    }
)

# ch15：协作工具也纳入子 Agent 全局剔除（团队成员经 extra_tools 绕回；普通子 Agent
# 即使在 TeamCreate 后也不可见——TD-2 动态注册进主 registry 的副作用防护）
GLOBAL_DENY = GLOBAL_DENY | TEAMMATE_EXTRA_TOOLS

# F6.3：后台工作者工具白名单——不含 agent 自身（B2 层 2）；mcp__* 前缀动态识别；
# load_skill 经 is_system_tool 豁免恒可见
ASYNC_AGENT_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "list_files",
        "search_code",
        "execute_command",
        "read_memory",
        "write_memory",
    }
)


@dataclass
class FilterParams:
    """apply_agent_tool_filter 的输入（F6.4）。"""

    all: list[str]  # registry 全部工具名（按注册顺序）
    background: bool  # 是否后台工作者
    role_tools: list[str] = field(
        default_factory=list
    )  # 定义 tools 白名单（空=不限制）
    role_disallowed: list[str] = field(
        default_factory=list
    )  # 定义 disallowedTools 黑名单


def is_mcp_tool(name: str) -> bool:
    """MCP 工具按命名约定动态识别（mcp__ 前缀，F6.3）。"""
    return name.startswith("mcp__")


def apply_agent_tool_filter(p: FilterParams) -> list[str]:
    """spec F6.4 顺序过滤，返回最终可见工具名列表（保持原注册顺序）。

    起点 = 全部 → 减 GLOBAL_DENY → 减定义层黑名单 → 白名单非空则交集 →
    后台则与 ASYNC_AGENT_ALLOWED_TOOLS(+mcp__*) 交集 → 追加系统工具豁免。
    """
    visible: list[str] = []
    for name in p.all:
        if name in GLOBAL_DENY:
            continue
        if name in p.role_disallowed:
            continue
        if p.role_tools and name not in p.role_tools:
            continue
        if (
            p.background
            and name not in ASYNC_AGENT_ALLOWED_TOOLS
            and not is_mcp_tool(name)
        ):
            continue
        visible.append(name)
    # 系统工具豁免恒可见（is_system_tool 名单兜底，F3.5 语义扩展到子 Agent 过滤）
    for name in SYSTEM_TOOL_NAMES:
        if name not in visible and name in p.all:
            visible.append(name)
    return visible
