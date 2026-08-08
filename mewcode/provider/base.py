"""LLM 协议无关类型：Provider Protocol、StreamEvent、Message、ToolCall、ToolDefinition、ToolResult"""

from dataclasses import dataclass, field
from typing import Literal, Protocol, AsyncIterator

from ..config.schema import ProviderConfig
from ..utils.error import ConfigError


@dataclass
class Message:
    """单条对话消息"""
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    tool_calls: list[dict] | None = None   # assistant 工具调用声明（协议无关）
    tool_call_id: str | None = None   # OpenAI 回灌需要
    tool_use_id: str | None = None    # Anthropic 回灌需要
    name: str | None = None           # OpenAI tool 角色需要 tool name


@dataclass
class ToolCall:
    """模型请求调用的工具"""
    tool_name: str
    arguments: dict = field(default_factory=dict)   # 已解析的 JSON 参数字典
    tool_use_id: str | None = None   # Anthropic 回灌需要
    tool_call_id: str | None = None  # OpenAI 回灌需要


@dataclass
class ToolDefinition:
    """工具的 API 定义（协议无关）"""
    name: str
    description: str
    parameters: dict = field(default_factory=dict)   # JSON Schema object


@dataclass
class ToolResult:
    """工具执行结果"""
    status: Literal["ok", "error"]
    output: str = ""
    error: str = ""
    truncated: bool = False


@dataclass
class StreamEvent:
    """流式事件：text / tool_call / done / err 互斥"""
    text: str = ""                             # 文本增量
    tool_call: ToolCall | None = None          # 工具调用（新增）
    done: bool = False                         # 本轮正常结束
    err: Exception | None = None               # 出错（与 done 互斥）


class Provider(Protocol):
    """LLM Provider 协议，所有后端通过此接口统一调用"""

    @property
    def name(self) -> str:
        """Provider 名称，用于状态栏左"""
        ...

    @property
    def model(self) -> str:
        """模型名，用于状态栏右"""
        ...

    def stream(
        self,
        msgs: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """发起一轮流式对话；内部注入 system prompt 与 thinking 配置；
        思考增量内部丢弃；以 async generator 吐出 StreamEvent；
        调用方 cancel() 该 task 即终止。
        """
        ...


def new_provider(cfg: ProviderConfig) -> Provider:
    """按 protocol 构造适配器"""
    if cfg.protocol == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(cfg)
    elif cfg.protocol == "openai":
        from .openai import OpenAIProvider
        return OpenAIProvider(cfg)
    else:
        raise ConfigError(f"Unknown protocol: {cfg.protocol}")