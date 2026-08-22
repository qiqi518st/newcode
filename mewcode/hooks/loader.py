"""Hook 三层配置加载、合并与校验（ch12 F6）：fail-soft。

本地临时 > 项目级 > 用户级 追加合并；任一文件缺失跳过；单条 hook 校验失败
stderr 定位（文件 + 条目 + 字段）并跳过，其余正常加载，进程正常启动（N1/N2/N3）。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from ..permission.matcher import matcher_from_spec
from .conditions import AtomCondition, Condition
from .engine import Engine
from .types import (
    Action,
    ActionType,
    AgentAction,
    CombineMode,
    Event,
    Hook,
    HttpAction,
    PromptAction,
    ShellAction,
    is_blocking,
)

# 文件位置（F6.1，三层）：本地 > 项目 > 用户
HOOK_FILE_LOCAL = ".mewcode/config.local.yaml"  # 本地（最高优先级），与权限 permissions.local.yaml 对齐
HOOK_FILE_PROJECT = ".mewcode/config.yaml"  # 项目级
HOOK_FILE_USER = os.path.expanduser("~/.mewcode/config.yaml")  # 用户级

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*$")


def _parse_duration(s: Any, default: float = 30.0) -> float | None:
    """解析时长字符串（F2.2 timeout）：'30s'/'5m'/'1.5'（浮点秒）或数字；
    非法 → None（调用方报错跳过该条）；缺省 30.0。"""
    if s is None:
        return default
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return float(s)
    if not isinstance(s, str):
        return None
    m = _DURATION_RE.match(s)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2)
    mult = {"s": 1.0, "m": 60.0, "h": 3600.0}.get(unit, 1.0)
    return value * mult


def _err_hook(name: str, file: str, reason: str) -> None:
    """加载错误 stderr 定位（N3）：文件 + 条目 + 字段。"""
    print(f'hook "{name}" (in {file}): {reason}, skipped', file=sys.stderr)


def _build_condition(spec: Any, name: str, file: str) -> Condition | None:
    """解析 if 对象（F4.1/F4.2/F4.5）；失败 stderr 定位并返回 None。

    顶层只能出现 all_of / any_of 之一；match 取四种类型（matcher_from_spec）。
    """
    if spec is None:
        return None
    if not isinstance(spec, dict):
        _err_hook(name, file, "if 必须是对象（含 all_of / any_of 之一）")
        return None
    present = [k for k in ("all_of", "any_of") if k in spec]
    if len(present) != 1:
        _err_hook(name, file, "if 顶层必须且只能出现 all_of / any_of 之一")
        return None
    mode = CombineMode(present[0])
    atoms_raw = spec.get(present[0])
    if not isinstance(atoms_raw, list):
        _err_hook(name, file, f"if.{present[0]} 必须是数组")
        return None
    atoms: list[AtomCondition] = []
    for i, item in enumerate(atoms_raw):
        if not isinstance(item, dict) or "field" not in item or "match" not in item:
            _err_hook(name, file, f"if.{present[0]}[{i}] 必须含 field 与 match")
            return None
        field = item.get("field")
        if not isinstance(field, str) or not field:
            _err_hook(name, file, f"if.{present[0]}[{i}].field 必须是非空字符串")
            return None
        try:
            matcher = matcher_from_spec(item.get("match"))
        except ValueError as e:
            _err_hook(name, file, f"if.{present[0]}[{i}].match 非法: {e}")
            return None
        atoms.append(AtomCondition(field=field, matcher=matcher))
    return Condition(mode=mode, atoms=atoms)


def _parse_action(action_raw: Any, name: str, file: str) -> Action | None:
    """解析 action 对象（F6.5）：type 四选一 + 各类型必填字段。"""
    if not isinstance(action_raw, dict):
        _err_hook(name, file, "action 必须是对象")
        return None
    atype_raw = action_raw.get("type")
    try:
        atype = ActionType(str(atype_raw)) if isinstance(atype_raw, str) else None
    except ValueError:
        atype = None
    if atype is None:
        _err_hook(name, file, f'unknown action type "{atype_raw}"')
        return None

    if atype == ActionType.COMMAND:
        command = action_raw.get("command")
        if not isinstance(command, str) or not command:
            _err_hook(name, file, "command 动作缺少必填字段 command")
            return None
        return Action(type=atype, shell=ShellAction(command=command))
    if atype == ActionType.PROMPT:
        text = action_raw.get("text")
        if not isinstance(text, str) or not text:
            _err_hook(name, file, "prompt 动作缺少必填字段 text")
            return None
        return Action(type=atype, prompt=PromptAction(text=text))
    if atype == ActionType.HTTP:
        url = action_raw.get("url")
        if not isinstance(url, str) or not url:
            _err_hook(name, file, "http 动作缺少必填字段 url")
            return None
        method = action_raw.get("method", "POST")
        if not isinstance(method, str) or not method:
            _err_hook(name, file, "http 动作的 method 必须是字符串")
            return None
        headers = action_raw.get("headers", {})
        if not isinstance(headers, dict):
            _err_hook(name, file, "http 动作的 headers 必须是键值对")
            return None
        body = action_raw.get("body")
        if body is not None and not isinstance(body, str):
            _err_hook(name, file, "http 动作的 body 必须是字符串模板")
            return None
        return Action(
            type=atype,
            http=HttpAction(
                url=url,
                method=method,
                headers={str(k): str(v) for k, v in headers.items()},
                body=body,
            ),
        )
    if atype == ActionType.AGENT:
        agent_name = action_raw.get("agent_name")
        prompt = action_raw.get("prompt")
        if not isinstance(agent_name, str) or not agent_name:
            _err_hook(name, file, "agent 动作缺少必填字段 agent_name")
            return None
        if not isinstance(prompt, str) or not prompt:
            _err_hook(name, file, "agent 动作缺少必填字段 prompt")
            return None
        return Action(type=atype, agent=AgentAction(agent_name=agent_name, prompt=prompt))
    return None  # 不可达


def _parse_hook(raw: Any, file: str, index: int) -> Hook | None:
    """解析单条 hook（F6.3/F6.5/F6.6）；失败 stderr 定位并返回 None。"""
    if not isinstance(raw, dict):
        _err_hook(f"<{file}#{index}>", file, "hook 条目必须是对象")
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        _err_hook(f"<{file}#{index}>", file, "name 必填且为非空字符串")
        return None

    event_raw = raw.get("event")
    try:
        event = Event(str(event_raw)) if isinstance(event_raw, str) else None
    except ValueError:
        event = None
    if event is None:
        _err_hook(name, file, f'unknown event "{event_raw}"')
        return None

    action = _parse_action(raw.get("action"), name, file)
    if action is None:
        return None  # 已 stderr 报错

    condition: Condition | None = None
    if "if" in raw:
        condition = _build_condition(raw.get("if"), name, file)
        if condition is None:
            return None  # 已 stderr 报错

    once = raw.get("once", False)
    if not isinstance(once, bool):
        _err_hook(name, file, "once 必须是布尔值")
        return None

    asyncio_mode = raw.get("async", False)
    if not isinstance(asyncio_mode, bool):
        _err_hook(name, file, "async 必须是布尔值")
        return None
    if asyncio_mode and is_blocking(event):
        _err_hook(name, file, "async not allowed for blocking events")
        return None

    timeout = _parse_duration(raw.get("timeout"))
    if timeout is None:
        _err_hook(name, file, f"timeout 格式非法: {raw.get('timeout')!r}")
        return None

    return Hook(
        name=name,
        event=event,
        action=action,
        condition=condition,
        once=once,
        asyncio_mode=asyncio_mode,
        timeout_s=timeout,
        source=file,
    )


def _load_file(path: Path, seen_names: set[str]) -> tuple[list[Hook], bool]:
    """加载单个 YAML 文件；缺失 → ([], False)；整体非法 → 告警 + 空（N2）。"""
    if not path.exists():
        return [], False
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"警告: Hook 配置文件格式错误 ({path}): {e}", file=sys.stderr)
        return [], True
    except OSError as e:
        print(f"警告: Hook 配置文件读取失败 ({path}): {e}", file=sys.stderr)
        return [], True
    if raw is None:
        return [], True
    if not isinstance(raw, dict):
        print(f"警告: Hook 配置顶层必须是对象 ({path})", file=sys.stderr)
        return [], True
    hooks_raw = raw.get("hooks", [])
    if not isinstance(hooks_raw, list):
        print(f"警告: hooks 必须是数组 ({path})", file=sys.stderr)
        return [], True

    rules: list[Hook] = []
    for i, item in enumerate(hooks_raw):
        rule = _parse_hook(item, str(path), i)
        if rule is None:
            continue
        if rule.name in seen_names:
            # F6.4：同名 hook 冲突 → 保留高优先级层（先加载者），跳过后到者
            _err_hook(rule.name, str(path), "name 与已加载 hook 冲突")
            continue
        seen_names.add(rule.name)
        rules.append(rule)
    return rules, True


def load(project_root: str | Path) -> Engine:
    """本地 → 项目 → 用户 依次加载、追加合并（F6.2，优先级高者在前）。

    返回 Engine（内部含来源文件列表）；所有错误走 stderr 不抛异常（N1/N2）。
    """
    root = Path(project_root)
    paths = [
        root / HOOK_FILE_LOCAL,
        root / HOOK_FILE_PROJECT,
        Path(HOOK_FILE_USER),
    ]
    rules: list[Hook] = []
    sources: list[str] = []
    seen_names: set[str] = set()
    for path in paths:
        file_rules, loaded = _load_file(path, seen_names)
        if loaded:
            sources.append(str(path))
        rules.extend(file_rules)
    return Engine(rules=rules, sources=sources)
