"""Agent 定义文件解析（ch13 F2）：frontmatter + 正文分离与字段校验。

frontmatter 切分复用 skills/parser.py 的 parse_frontmatter_and_body（不重复实现）。
失败策略：字段非法按 spec F2.4 降级缺省 + stderr 警告；结构非法（缺 description /
name 非法 / frontmatter 未闭合）抛 DefinitionParseError，由 Catalog 决定 skip 或 raise。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from ..permission.modes import PermissionMode
from ..skills.parser import parse_frontmatter_and_body
from .types import DEFAULT_MAX_TURNS, AgentDefinition, DefinitionParseError, Source

# 角色名归一化后合法性（与 subagent_type 取值一致，spec F2.1）
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

_MODEL_VALUES = frozenset({"inherit", "haiku", "sonnet", "opus"})


def _warn(path: str, msg: str) -> None:
    print(f"subagent {path}: {msg}", file=sys.stderr)


def parse_definition(path: str, source: Source) -> AgentDefinition:
    """解析一个 `.md` 角色文件为 AgentDefinition（spec F2.1/F2.4）。"""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise DefinitionParseError(str(p), f"cannot read: {exc}") from exc
    return parse_definition_text(raw, source, str(p))


def parse_definition_text(raw: str, source: Source, display_path: str) -> AgentDefinition:
    """从文本解析（内置随包数据走此入口，display_path 供诊断定位，spec F2.4）。"""
    try:
        meta, body = parse_frontmatter_and_body(raw)
    except Exception as exc:
        # 复用 skills 抛错统一包装为 DefinitionParseError（spec F2.4 可诊断）
        raise DefinitionParseError(display_path, f"frontmatter 解析失败: {exc}") from exc

    # name：缺省取 display_path 基名（磁盘文件）或空；非法抛错（结构性问题）
    basename = Path(display_path).stem if display_path.endswith(".md") else ""
    name = str(meta.get("name", "")).strip() or basename
    if not _NAME_RE.match(name):
        raise DefinitionParseError(
            display_path, f"invalid name {name!r} (must match ^[a-z][a-z0-9-]*$)"
        )

    # description 必填
    description = str(meta.get("description", "")).strip()
    if not description:
        raise DefinitionParseError(
            display_path, "missing required frontmatter field: description"
        )

    # tools / disallowedTools：非 list → 警告置空
    tools = _as_name_list(meta.get("tools"), display_path, "tools")
    disallowed = _as_name_list(
        meta.get("disallowedTools"), display_path, "disallowedTools"
    )

    # model：非法降级 inherit（F2.4）
    model = str(meta.get("model") or "").strip() or "inherit"
    if model not in _MODEL_VALUES:
        _warn(display_path, f"unknown model {model!r}, falling back to inherit")
        model = "inherit"

    # permissionMode：dontAsk 单独识别（F5.3），非法降级 default
    pm_raw = str(meta.get("permissionMode") or "").strip()
    dont_ask = False
    if pm_raw.lower() == "dontask":
        permission_mode = PermissionMode.DEFAULT
        dont_ask = True
    else:
        permission_mode = (
            PermissionMode.parse(pm_raw) if pm_raw else PermissionMode.DEFAULT
        )
        if permission_mode is None:
            _warn(
                display_path,
                f"unknown permissionMode {pm_raw!r}, falling back to default",
            )
            permission_mode = PermissionMode.DEFAULT

    # maxTurns / background / enabled：类型非法 → 警告缺省
    max_turns = _int_field(meta.get("maxTurns"), DEFAULT_MAX_TURNS, display_path, "maxTurns")
    background = _bool_field(meta.get("background"), False, display_path, "background")
    enabled = _bool_field(meta.get("enabled"), True, display_path, "enabled")

    return AgentDefinition(
        name=name,
        description=description,
        body=body,
        tools=tools,
        disallowed_tools=disallowed,
        model=model,
        max_turns=max_turns,
        permission_mode=permission_mode,
        dont_ask=dont_ask,
        background=background,
        enabled=enabled,
        source=source,
        source_path=display_path,
    )


def _as_name_list(value: object, path: str, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(t).strip() for t in value if str(t).strip()]
    _warn(path, f"{key} 非 list，置空")
    return []


def _int_field(value: object, default: int, path: str, key: str) -> int:
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    _warn(path, f"{key}={value!r} 非法，使用缺省 {default}")
    return default


def _bool_field(value: object, default: bool, path: str, key: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    _warn(path, f"{key}={value!r} 非法，使用缺省 {default}")
    return default
