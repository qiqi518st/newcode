"""YAML 配置加载、校验、${ENV_VAR} 解析、CC Switch 自动检测"""

import json
import os
import re
from pathlib import Path

import yaml

from ..utils.error import ConfigError
from .schema import Config, ProviderConfig

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def load(path: str) -> Config:
    """加载并校验 YAML 配置文件"""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(
            f"配置文件不存在：{path}\n"
            f"请复制项目根目录的 .newcode.yaml.example 为 .newcode.yaml 并填写配置。"
        )

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件 YAML 格式错误: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError("配置文件必须是 YAML 字典格式")

    return _parse(raw)


def _parse(raw: dict) -> Config:
    """解析原始 YAML 字典为 Config 对象"""
    provider_name = raw.get("provider", "")
    max_turns = raw.get("max_turns", 20)
    system_prompt = raw.get("system_prompt", "")
    cleanup_period_days = raw.get("cleanup_period_days", 30)
    default_mode = raw.get("default_mode", "normal")

    raw_providers = raw.get("providers", [])
    if not isinstance(raw_providers, list):
        raise ConfigError("providers 字段必须是列表")

    providers: list[ProviderConfig] = []
    for item in raw_providers:
        name = _require_field(item, "name")
        protocol = _require_field(item, "protocol")
        model = _require_field(item, "model")
        api_key = _resolve_env(item.get("api_key", ""))
        auth_token = (
            _resolve_env(item["auth_token"]) if item.get("auth_token") else None
        )
        base_url = item.get("base_url")  # 可选
        thinking = item.get("thinking", False)

        if not api_key and not auth_token:
            raise ConfigError(
                f"provider '{name}' 缺少认证信息：api_key 或 auth_token 至少填一个"
            )

        if protocol not in ("anthropic", "openai"):
            raise ConfigError(
                f"provider '{name}' 的 protocol 必须是 'anthropic' 或 'openai'，当前为 '{protocol}'"
            )

        providers.append(
            ProviderConfig(
                name=name,
                protocol=protocol,
                model=model,
                api_key=api_key,
                auth_token=auth_token,
                base_url=base_url,
                thinking=thinking,
            )
        )

    if not providers:
        raise ConfigError("providers 列表不能为空")

    _validate(
        config=Config(
            provider=provider_name,
            max_turns=max_turns,
            system_prompt=system_prompt,
            cleanup_period_days=cleanup_period_days,
            default_mode=default_mode,
            providers=providers,
        )
    )

    return Config(
        provider=provider_name,
        max_turns=max_turns,
        system_prompt=system_prompt,
        cleanup_period_days=cleanup_period_days,
        default_mode=default_mode,
        providers=providers,
    )


def _require_field(item: dict, field: str) -> str:
    """获取必填字段，缺失则抛出 ConfigError"""
    value = item.get(field)
    if not value:
        raise ConfigError(f"provider 配置缺少必填字段 '{field}'")
    return str(value)


def _resolve_env(value: str) -> str:
    """解析 ${ENV_VAR} 占位符"""

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            raise ConfigError(
                f"环境变量 '{var_name}' 未设置，无法解析配置中的 ${{{var_name}}}"
            )
        return env_value

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _validate(config: Config) -> None:
    """校验配置完整性"""
    provider_names = {p.name for p in config.providers}
    if config.provider not in provider_names:
        raise ConfigError(
            f"provider '{config.provider}' 不在 providers 列表中，"
            f"可用 provider: {', '.join(provider_names)}"
        )


def load_ccswitch() -> Config | None:
    """检测 CC Switch / Claude Code 共享配置。

    Claude Code 的配置目录由 CLAUDE_CONFIG_DIR 指定（未设置时回退 ~/.claude），
    其 settings.json 的 env 字段保存 API key 与端点。这里同时认
    ANTHROPIC_API_KEY（x-api-key）和 ANTHROPIC_AUTH_TOKEN（Bearer），
    与 Claude Code 实际使用的字段一致，避免手动复制 key 到 .newcode.yaml。
    检测到有效配置则直接返回 Config 对象。
    """
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    settings_path = (
        Path(config_dir) / "settings.json"
        if config_dir
        else Path.home() / ".claude" / "settings.json"
    )
    if not settings_path.exists():
        return None

    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    env = raw.get("env", {})
    if not isinstance(env, dict):
        return None

    api_key = env.get("ANTHROPIC_API_KEY", "")
    auth_token = env.get("ANTHROPIC_AUTH_TOKEN", "")

    if not api_key and not auth_token:
        return None

    base_url = env.get("ANTHROPIC_BASE_URL", "")
    model = env.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    return Config(
        provider="ccswitch",
        providers=[
            ProviderConfig(
                name="ccswitch",
                protocol="anthropic",
                model=model,
                api_key=api_key,
                auth_token=auth_token or None,
                base_url=base_url or None,
                thinking=False,
            )
        ],
    )
