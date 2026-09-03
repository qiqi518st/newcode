"""SubAgent 数据结构（ch13）：AgentDefinition / Source / 常量与文案。

- AgentDefinition：一个角色（子 Agent 定义），从 Markdown + YAML frontmatter 解析，
  body 即子 Agent 系统提示（spec F2.1）
- Source：加载来源与优先级（项目 > 用户 > 内置 > 插件，spec F2.2/F2.3）
- DefinitionParseError：解析失败（携带文件与原因，供 stderr 定位，spec F2.4/F2.6）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from ..permission.modes import PermissionMode

# 子 Agent 缺省最大迭代轮数（spec F2.1/F11.1：agents.max_turns 全局默认，可配置）
DEFAULT_MAX_TURNS: int = 15

# 注入主对话的 <task-notification> result 字段截断上限（spec F7.6/N6）
RESULT_TRUNCATE_CHARS: int = 800

# 完成通知 XML 模板（spec F7.6，user 角色注入主对话）
NOTIFICATION_XML = """<task-notification>
<task-id>{task_id}</task-id>
<status>{status}</status>
<summary>{summary}</summary>
<result>{result}</result>
</task-notification>"""


class Source(IntEnum):
    """角色加载来源（优先级由加载顺序表达：高优先级先写、后写不覆盖已有，spec F2.2）。"""

    BUILTIN = 0
    USER = 1
    PROJECT = 2
    PLUGIN = 3  # 占位：本期无真插件系统，恒为空

    def __str__(self) -> str:
        return {0: "builtin", 1: "user", 2: "project", 3: "plugin"}.get(
            int(self), "unknown"
        )


@dataclass
class AgentDefinition:
    """一个角色（子 Agent 定义）的完整描述（spec F2.1）。"""

    name: str  # 角色名（subagent_type 取值），^[a-z][a-z0-9-]*$，1-32
    description: str  # 用途说明（必填；Agent 工具 subagent_type 文档与 UI 列表用）
    body: str = ""  # 正文 = 子 Agent 系统提示（身份/职责/工作风格）
    tools: list[str] = field(
        default_factory=list
    )  # 工具白名单（空 = 不限制，spec F2.1）
    disallowed_tools: list[str] = field(
        default_factory=list
    )  # 工具黑名单（spec F2.1/F6.2）
    model: str = (
        "inherit"  # inherit/haiku/sonnet/opus（命名分层经配置映射，F2.1/F11.1）
    )
    max_turns: int = (
        0  # 最大迭代轮数；0=未设置 → 回落 agents.max_turns 全局默认（F2.1/F11.1）
    )
    permission_mode: PermissionMode = PermissionMode.DEFAULT  # 四档（F5.3）
    dont_ask: bool = False  # frontmatter permissionMode: dontAsk → True（F5.3）
    background: bool = False  # 角色强制后台（F2.1）
    enabled: bool = True  # False 时不加载（内置 verifier 用，F2.5）
    isolation: str = ""  # ch14：""=不隔离 / "worktree"=Git Worktree 文件隔离（F5.1）
    plan_mode_required: bool = (
        False  # ch15：spawn 进 Team 时以 plan 模式起步（F48/F13.1）
    )
    source: Source = Source.BUILTIN  # 来源（诊断与过滤用，F2.2/F6.4）
    source_path: str = ""  # 来源文件绝对路径（诊断用）

    def is_fork(self) -> bool:
        """是否为 Fork 路径伪定义（fork_definition 的 name="__fork__"，plan F6 设计）。"""
        return self.name == "__fork__"


class DefinitionParseError(Exception):
    """角色定义解析失败（携带 path/reason，调用方 Catalog 决定 skip 或 raise，spec F2.4）。"""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"subagent {path}: {reason}")
