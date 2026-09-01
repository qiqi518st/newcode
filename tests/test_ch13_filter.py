"""ch13 tools/filter.py 多层防线测试。

防的 bug：
- GLOBAL_DENY 漏剔 agent → 子 Agent 可见 agent 工具（嵌套失控）
- 后台交集漏 mcp__* → 后台子 Agent 失去网络能力（F6.3 语义）
- 黑/白名单组合顺序错（白名单先收窄、黑名单再剔除）
- 系统工具（load_skill）豁免丢失 → 子 Agent 无法加载 Skill
"""

from __future__ import annotations

from mewcode.tools.filter import (
    ASYNC_AGENT_ALLOWED_TOOLS,
    GLOBAL_DENY,
    FilterParams,
    apply_agent_tool_filter,
    is_mcp_tool,
)

ALL = [
    "agent",
    "read_file",
    "write_file",
    "edit_file",
    "list_files",
    "search_code",
    "execute_command",
    "read_memory",
    "write_memory",
    "mcp__web",
    "load_skill",
]


def test_default_removes_agent():
    r = apply_agent_tool_filter(FilterParams(all=ALL, background=False))
    assert "agent" not in r
    assert "read_file" in r and "execute_command" in r


def test_background_intersects_whitelist():
    r = apply_agent_tool_filter(FilterParams(all=ALL, background=True))
    assert "agent" not in r  # B2 层 2：后台白名单不含 agent
    assert "read_file" in r
    assert "mcp__web" in r  # mcp__* 保留
    assert "load_skill" in r  # 系统豁免
    assert set(r) <= (set(ASYNC_AGENT_ALLOWED_TOOLS) | {"mcp__web", "load_skill"})


def test_disallowed_removed_even_if_whitelisted():
    r = apply_agent_tool_filter(
        FilterParams(
            all=ALL,
            background=False,
            role_tools=["read_file", "execute_command"],
            role_disallowed=["execute_command"],
        )
    )
    # 白名单先收窄、黑名单再剔除；系统工具（load_skill）豁免恒在
    assert r == ["read_file", "load_skill"]


def test_whitelist_limits():
    r = apply_agent_tool_filter(
        FilterParams(all=ALL, background=False, role_tools=["read_file", "list_files"])
    )
    assert r == ["read_file", "list_files", "load_skill"]  # 仅白名单 + 系统工具


def test_order_stable():
    r = apply_agent_tool_filter(FilterParams(all=ALL, background=False))
    assert r == [n for n in ALL if n in r]  # 保持注册顺序


def test_is_mcp_tool():
    assert is_mcp_tool("mcp__web") is True
    assert is_mcp_tool("read_file") is False


def test_global_deny_is_fixed():
    assert GLOBAL_DENY == frozenset({"agent"})
