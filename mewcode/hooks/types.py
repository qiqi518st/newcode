"""Hook 系统数据结构与常量（ch12 F2/F3）。

Event/Action/Hook/Payload/DispatchResult/ExecutionResult 定义在此；
条件表达式（Condition/AtomCondition）在 conditions.py（避免 types→conditions 循环导入，
Hook.condition 以 TYPE_CHECKING 标注）；动作执行在 executor.py。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .conditions import Condition


class Event(str, enum.Enum):
    """18 个生命周期事件（spec F3.1），snake_case，YAML 字面量直接对应。"""

    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SESSION_RESUME = "session_resume"
    USER_PROMPT_SUBMIT = "user_prompt_submit"  # 拦截
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    PRE_TOOL_USE = "pre_tool_use"  # 拦截
    POST_TOOL_USE = "post_tool_use"
    PRE_SEND = "pre_send"
    POST_RECEIVE = "post_receive"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    ERROR = "error"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"
    PERMISSION_REQUEST = "permission_request"
    FILE_CHANGE = "file_change"
    COMMAND_EXECUTE = "command_execute"


BLOCKING_EVENTS: frozenset[Event] = frozenset(
    {Event.PRE_TOOL_USE, Event.USER_PROMPT_SUBMIT}
)


def is_blocking(e: Event) -> bool:
    """事件是否可拦截（仅 pre_tool_use / user_prompt_submit，spec F3.2）。"""
    return e in BLOCKING_EVENTS


class CombineMode(str, enum.Enum):
    """条件组合模式（F4.1）：all_of / any_of 二选一，顶层不嵌套混用。"""

    ALL_OF = "all_of"
    ANY_OF = "any_of"


class ActionType(str, enum.Enum):
    """动作类型（F5.1）：command / prompt / http / agent。"""

    COMMAND = "command"
    PROMPT = "prompt"
    HTTP = "http"
    AGENT = "agent"


@dataclass
class ShellAction:
    """command 动作：shell 子进程执行（sh -c），payload JSON 走 stdin（F5.2）。"""

    command: str


@dataclass
class PromptAction:
    """prompt 动作：注入下次 LLM 请求的 reminder 区（F5.6）。"""

    text: str


@dataclass
class HttpAction:
    """http 动作：发 HTTP 请求（F5.9）；body=None 时用 payload JSON。"""

    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None


@dataclass
class AgentAction:
    """agent 动作：本期占位（F5.13），仅校验字段完整 + 占位日志。"""

    agent_name: str
    prompt: str


@dataclass
class Action:
    """动作容器：type 决定哪个子结构生效（F5.1）。"""

    type: ActionType
    shell: ShellAction | None = None
    prompt: PromptAction | None = None
    http: HttpAction | None = None
    agent: AgentAction | None = None


@dataclass
class Hook:
    """一条 Hook 规则（F2.1）：event + if + action 三要素 + 执行控制。"""

    name: str  # 必填；日志/once/冲突检测
    event: Event  # 必填
    action: Action  # 必填
    condition: Condition | None = None  # if；None=无条件触发
    once: bool = False
    asyncio_mode: bool = False  # YAML 写 async；内部避关键字（Loader 映射）
    timeout_s: float = 30.0  # command/http 用
    source: str = ""  # 来源文件路径，/hooks 显示用


# 事件分派携带的上下文数据（F3.4）；序列化 JSON 用 json.dumps(payload, sort_keys=True)（N5）。
Payload = dict[str, Any]


@dataclass
class DispatchResult:
    """一次 dispatch 的汇总结果（F7）：拦截信号 + 待注入 prompt。"""

    blocked: bool = False
    reason: str = ""
    blocking_hook_name: str = ""
    injected_prompts: list[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    """单条 hook 动作执行结果（F5/F9.1）：blocked/reason/prompt/err。"""

    blocked: bool = False
    reason: str = ""
    prompt: str = ""  # 仅 prompt 动作非空
    err: Exception | None = None  # hook 自身失败（不拦截）
