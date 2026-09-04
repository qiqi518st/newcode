"""两层 YAML 配置加载、合并、${VAR} 展开、字段校验、非法 server 隔离。

纯函数式：load_mcp_servers 永不抛出——文件缺失/格式非法/type 非法/字段缺失
均降级为跳过该层或该 server 并 stderr 告警，决不阻断 NewCode 启动。

- 用户级：<home>/.newcode/config.yaml
- 项目级：<root>/.newcode.yaml
- 合并：按 server 名维度，项目级同名 server 完整覆盖用户级（不做字段级合并）。
- ${VAR} 仅展开 env / headers 的值；command / args / server 名 / 工具名不展开。
- 未定义 ${VAR} → 空串 + 一次性 stderr 告警（不阻断该 server 启动）。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

# 仅匹配「合法 shell 变量名」构成的 ${VAR}，避免误吞 ${} 类异常占位
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class ServerConfig:
    """单个 MCP server 的已校验、已展开定义（对外类型）。"""

    name: str  # 配置里的 key，原样保留（见 spec F2/F8）
    type: Literal["stdio", "http"]
    # stdio 专属
    command: str = ""  # stdio 必填非空
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)  # 已展开 ${VAR}
    # http 专属
    url: str = ""  # http 必填非空
    headers: dict[str, str] = field(default_factory=dict)  # 已展开 ${VAR}


@dataclass
class _RawServer:
    """从 YAML 读出的原始 server 定义，字段全部带默认（缺失填默认）。"""

    type: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


def _warn(msg: str) -> None:
    """统一 stderr 告警格式（便于测试 grep）。"""
    print(f"[mcp] warn: {msg}", file=sys.stderr)


def _load_file(path: Path) -> dict[str, _RawServer]:
    """读取单个 YAML 文件的 mcp_servers 段；任何异常 → 空字典 + 告警（不抛）。

    防回归：曾出现 YAML 格式错误直接抛 YAMLError 致 newcode 启动失败（spec F1/N1 要求绝不致启动失败）。
    """
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        _warn(f"load {path} failed: {e}")
        return {}
    except OSError as e:
        _warn(f"load {path} failed: {e}")
        return {}
    if not isinstance(raw, dict):
        return {}
    servers = raw.get("mcp_servers")
    if not isinstance(servers, dict):
        return {}
    result: dict[str, _RawServer] = {}
    for name, item in servers.items():
        if not isinstance(name, str) or not isinstance(item, dict):
            continue
        result[name] = _RawServer(
            type=str(item.get("type", "") or ""),
            command=str(item.get("command", "") or ""),
            args=list(item.get("args")) if isinstance(item.get("args"), list) else [],
            env=dict(item.get("env")) if isinstance(item.get("env"), dict) else {},
            url=str(item.get("url", "") or ""),
            headers=dict(item.get("headers"))
            if isinstance(item.get("headers"), dict)
            else {},
        )
    return result


def _expand_value(s: str, server_name: str, seen_undef: set[str]) -> str:
    """展开 ${VAR}；未定义变量 → 空串并记入 seen_undef（告警由调用方汇总一次性输出）。"""

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name not in os.environ:
            seen_undef.add(var_name)
            return ""
        return os.environ[var_name]

    return _ENV_VAR_PATTERN.sub(_replace, s)


def _apply_expansion(name: str, srv: _RawServer) -> None:
    """对 srv.env / srv.headers 的值就地展开 ${VAR}；同 server 同未定义变量限一次告警。"""
    seen_undef: set[str] = set()
    new_env: dict[str, str] = {}
    for k, v in srv.env.items():
        new_env[k] = _expand_value(v, name, seen_undef) if isinstance(v, str) else v
    srv.env = new_env
    new_headers: dict[str, str] = {}
    for k, v in srv.headers.items():
        new_headers[k] = _expand_value(v, name, seen_undef) if isinstance(v, str) else v
    srv.headers = new_headers
    for var in sorted(seen_undef):
        _warn(f"undefined env var ${{{var}}} referenced by server {name}")


def _merge_servers(
    user: dict[str, _RawServer], project: dict[str, _RawServer]
) -> dict[str, _RawServer]:
    """项目级同名 server 完整覆盖用户级（不做字段级合并，避免半合并出畸形 server）。"""
    return {**user, **project}


def _validate_server(name: str, srv: _RawServer) -> ServerConfig | None:
    """校验 type/必填字段；非法 → None + 告警。其余字段类型兜底后构造 ServerConfig。"""
    if srv.type not in ("stdio", "http"):
        _warn(f"skip server {name}: type must be 'stdio' or 'http', got {srv.type!r}")
        return None
    if srv.type == "stdio" and not srv.command:
        _warn(f"skip server {name}: stdio server requires non-empty 'command'")
        return None
    if srv.type == "http" and not srv.url:
        _warn(f"skip server {name}: http server requires non-empty 'url'")
        return None
    return ServerConfig(
        name=name,
        type=srv.type,  # type: ignore[arg-type]
        command=srv.command,
        args=list(srv.args),
        env=dict(srv.env),
        url=srv.url,
        headers=dict(srv.headers),
    )


def load_mcp_servers(root: str) -> dict[str, ServerConfig]:
    """加载用户级 + 项目级 MCP server 配置，合并、展开、校验，返回可用 server 字典。

    永不抛出。任一文件缺失/非法 → 跳过该层；任一 server 非法 → 跳过该 server。
    """
    # 用户级：<home>/.newcode/config.yaml（Path.home 抛错时跳过用户层）
    try:
        user_path = Path.home() / ".newcode" / "config.yaml"
    except RuntimeError as e:  # Path.home() 无法确定主目录时
        _warn(f"resolve home dir failed, skip user-level config: {e}")
        user_path = None  # type: ignore[assignment]
    project_path = Path(root) / ".newcode.yaml"

    user_servers: dict[str, _RawServer] = (
        _load_file(user_path) if user_path is not None else {}
    )
    project_servers = _load_file(project_path)

    # 各层先各自展开（未定义变量在该 server 各值间去重，跨层不共享 seen_undef）
    for name, srv in user_servers.items():
        _apply_expansion(name, srv)
    for name, srv in project_servers.items():
        _apply_expansion(name, srv)

    merged = _merge_servers(user_servers, project_servers)

    result: dict[str, ServerConfig] = {}
    for name, srv in merged.items():
        cfg = _validate_server(name, srv)
        if cfg is not None:
            result[name] = cfg
    return result
