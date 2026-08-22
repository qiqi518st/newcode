"""Hook 条件表达式（ch12 F4）：结构化 {field, match} 解析与求值。

Condition 复用 permission.matcher 的 Matcher；get_by_path 取 payload 点分字段路径。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..permission.matcher import Matcher, evaluate
from .types import CombineMode, Payload


@dataclass
class AtomCondition:
    """原子条件：field（payload 点分路径）+ matcher（匹配器，F4.2）。"""

    field: str
    matcher: Matcher


@dataclass
class Condition:
    """条件表达式：all_of / any_of 二选一组合原子条件（F4.1/F4.2）。"""

    mode: CombineMode
    atoms: list[AtomCondition]


def _stringify(value: Any) -> str:
    """把 payload 字段值转成匹配用的字符串（F4.3 值类型规则）：
    - str → 原样
    - bool → "true"/"false"（与 YAML 直觉及 spec 场景 1 的 is_error: false 一致）
    - int/float → str()
    - 嵌套 dict/list → json.dumps(sort_keys=True)（与 N5 稳定序列化一致）
    - None → ""（视为缺失）
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def get_by_path(payload: Payload, path: str) -> str:
    """按点分路径取 payload 字段（F4.3）：tool_input.path 遍历嵌套 dict；
    路径不存在 → ""，不报错；值经 _stringify 转字符串。"""
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return ""
        current = current[part]
    return _stringify(current)


def eval_condition(cond: Condition | None, payload: Payload) -> bool:
    """条件求值（F4.6）：cond=None → True（无条件触发）；
    否则逐一 evaluate(atom.matcher, get_by_path(...))，按 mode 做 all/any 组合。"""
    if cond is None:
        return True
    matched = [
        evaluate(atom.matcher, get_by_path(payload, atom.field)) for atom in cond.atoms
    ]
    if cond.mode == CombineMode.ALL_OF:
        return all(matched)
    return any(matched)
