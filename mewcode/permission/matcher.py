"""共享规则匹配器（ch12 前置基础 F1）：四种匹配类型统一接口。

ch08 权限规则的 Pattern 形态从单一字符串扩展为结构化 Matcher：
exact（精确整串）/ glob（通配，缺省类型）/ regex（正则）/ not（一元取反，可嵌套）。
Hook 条件表达式（mewcode/hooks/conditions.py）与权限规则（rules.py）共用本实现。

测试与实现的对应说明见 tests/test_ch12_matcher.py（防回归标注）。
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Protocol


class Matcher(Protocol):
    """规则匹配统一接口；四种实现：Exact / Glob / Regex / Not。"""

    def match(self, s: str) -> bool: ...

    def __str__(self) -> str: ...  # 调试 / /hooks 输出用


@dataclass(frozen=True)
class ExactMatcher:
    """精确整串相等（`=value`，对应 ==）。"""

    value: str

    def match(self, s: str) -> bool:
        return s == self.value

    def __str__(self) -> str:
        return f"={self.value}"


@dataclass(frozen=True)
class GlobMatcher:
    """通配匹配（缺省类型，向后兼容 `Bash(git *)` 语义）。

    is_command=True：Bash 命令整串通配（fnmatch 整串）；
    is_command=False：走 match_pattern 自动判断——含 `/` 或 `**` 按路径分段递归，
    否则 fnmatch 整串（hook 条件侧 `rm -rf *` 匹配命令串依赖此行为，spec 场景 2）。
    """

    pattern: str
    is_command: bool = False

    def match(self, s: str) -> bool:
        if self.is_command:
            return match_command(self.pattern, s)
        return match_pattern(self.pattern, s)

    def __str__(self) -> str:
        return self.pattern


@dataclass(frozen=True)
class RegexMatcher:
    """正则匹配（`~regex`，对应 ~）；加载期编译并缓存，match 用 .search 部分匹配。"""

    src: str
    compiled: re.Pattern[str]

    def match(self, s: str) -> bool:
        return self.compiled.search(s) is not None

    def __str__(self) -> str:
        return f"~{self.src}"


@dataclass(frozen=True)
class NotMatcher:
    """一元取反（`!inner`，对应 !=），支持嵌套（!=value / !~re / !glob）。"""

    inner: Matcher

    def match(self, s: str) -> bool:
        return not self.inner.match(s)

    def __str__(self) -> str:
        return f"!{self.inner}"


def match_command(pattern: str, target: str) -> bool:
    """Bash 命令整串通配（fnmatch 整串）；空 pattern 恒匹配。"""
    if pattern == "":
        return True
    return fnmatch.fnmatch(target, pattern)


def match_pattern(pattern: str, target: str) -> bool:
    """glob 匹配（原有语义，从 rules.py 移入 matcher.py 供两类调用方共用）：
    - pattern == "" → 恒匹配
    - 含 `/` 或 `**` → 按路径分段递归（`*` 单层、`**` 任意多层）
    - 其余 → fnmatch 整串通配
    """
    if pattern == "":
        return True
    if "/" in pattern or "**" in pattern:
        return _match_recursive(pattern, target)
    return fnmatch.fnmatch(target, pattern)


def _match_recursive(pattern: str, target: str) -> bool:
    """支持 ** 递归通配的匹配"""
    pat_parts = pattern.replace("\\", "/").split("/")
    tgt_parts = target.replace("\\", "/").split("/")
    return _match_parts(pat_parts, tgt_parts)


def _match_parts(pat_parts: list[str], tgt_parts: list[str]) -> bool:
    """递归匹配路径段"""
    if not pat_parts:
        return not tgt_parts
    if pat_parts[0] == "**":
        if len(pat_parts) == 1:
            return True  # ** 匹配一切
        # ** 匹配零段或多段
        for i in range(len(tgt_parts) + 1):
            if _match_parts(pat_parts[1:], tgt_parts[i:]):
                return True
        return False
    if not tgt_parts:
        return False
    if fnmatch.fnmatch(tgt_parts[0], pat_parts[0]):
        return _match_parts(pat_parts[1:], tgt_parts[1:])
    return False


def _compile_regex(src: str) -> re.Pattern[str]:
    """编译正则；失败统一转 ValueError（re.error 不是 ValueError，加载层按 ValueError 捕获）。"""
    try:
        return re.compile(src)
    except re.error as e:
        raise ValueError(f"invalid regex {src!r}: {e}") from e


def compile_matcher(pattern: str, *, is_command: bool = False) -> Matcher:
    """解析单条匹配描述串（F1.2/F1.3），失败抛 ValueError。

    "=value"  -> ExactMatcher
    "~regex"  -> RegexMatcher（编译失败 -> ValueError）
    "!inner"  -> NotMatcher(compile_matcher(inner))   # 支持 !=value / !~re / !glob
    "value"   -> GlobMatcher（缺省，向后兼容）
    ""        -> ValueError（无括号规则由 Rule.parse 置 matcher=None，见 rules.py）
    """
    if not isinstance(pattern, str) or pattern == "":
        raise ValueError("empty match pattern")
    if pattern.startswith("="):
        return ExactMatcher(value=pattern[1:])
    if pattern.startswith("~"):
        src = pattern[1:]
        return RegexMatcher(src=src, compiled=_compile_regex(src))
    if pattern.startswith("!"):
        return NotMatcher(inner=compile_matcher(pattern[1:], is_command=is_command))
    return GlobMatcher(pattern=pattern, is_command=is_command)


def matcher_from_spec(d: object, *, is_command: bool = False) -> Matcher:
    """Hook 条件 YAML -> Matcher（F4.4）：
      {type: exact|glob|regex, value} / {type: not, inner: {...}}
    不合法抛 ValueError。glob 复用 match_pattern 自动判断语义（见 GlobMatcher）。
    """
    if not isinstance(d, dict):
        raise ValueError(  # noqa: TRY004 —— 调用方（hooks loader）统一按 ValueError 捕获
            f"match spec must be a dict, got {type(d).__name__}"
        )
    mtype = d.get("type")
    if mtype == "not":
        inner = d.get("inner")
        if not isinstance(inner, dict):
            raise ValueError("'not' match requires 'inner'")
        return NotMatcher(inner=matcher_from_spec(inner, is_command=is_command))
    value = d.get("value")
    if not isinstance(value, str):
        raise ValueError(  # noqa: TRY004 —— 同上，加载层统一按 ValueError 捕获
            f"match spec type={mtype!r} requires a string 'value'"
        )
    if mtype == "exact":
        return ExactMatcher(value=value)
    if mtype == "glob":
        return GlobMatcher(pattern=value, is_command=is_command)
    if mtype == "regex":
        return RegexMatcher(src=value, compiled=_compile_regex(value))
    raise ValueError(f"unknown match type: {mtype!r}")


def evaluate(spec: Matcher, target: str) -> bool:
    """等价 spec.match(target)，保持调用点语义统一。"""
    return spec.match(target)
