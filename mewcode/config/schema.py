"""配置数据类"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ProviderConfig:
    """单个 LLM 供应商的配置"""

    name: str  # 状态栏左侧显示
    protocol: Literal["anthropic", "openai"]  # 协议类型
    model: str  # 状态栏右侧显示
    api_key: str = ""  # API key 认证（x-api-key header）
    auth_token: str | None = None  # Bearer token 认证（Authorization header），
    # 如 CC Switch 的 ANTHROPIC_AUTH_TOKEN。
    # 有值时优先用 Bearer，否则用 api_key。
    base_url: str | None = None  # None 则用 SDK 默认端点
    thinking: bool = False  # 仅 anthropic 生效


@dataclass
class Config:
    """MewCode 全局配置"""

    provider: str  # 当前激活的 provider name
    max_turns: int = 20  # 滑动窗口保留轮数
    system_prompt: str = ""  # 自定义 system prompt，空则用内置默认值
    cleanup_period_days: int = 30  # 计划文件清理周期（天），0 表示不清理
    default_mode: str = "normal"  # 默认运行模式："normal" | "plan"
    permission_mode: str = "default"  # 启动默认权限模式
    providers: list[ProviderConfig] = field(default_factory=list)
