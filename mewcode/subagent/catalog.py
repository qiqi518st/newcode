"""Agent 定义 Catalog（ch13 F2）：四层加载 + 优先级覆盖 + fork 伪定义。

加载顺序（高优先级先写，后扫到的同名只写入 key 尚未存在的——前者覆盖后者）：
  项目级 <root>/.mewcode/agents → 用户级 ~/.mewcode/agents → 内置
  mewcode/subagent/builtin（importlib.resources）→ 插件级（本期跳过，占位）。

失败策略（N4/F2.4）：内置级解析失败 raise（代码 bug）；用户/项目级单文件失败
stderr 定位并跳过，其余正常加载。
"""

from __future__ import annotations

import importlib.resources
import sys
import threading
from pathlib import Path

from ..permission.modes import PermissionMode
from .config import AgentConfig
from .parser import parse_definition, parse_definition_text
from .types import (
    DEFAULT_MAX_TURNS,
    AgentDefinition,
    DefinitionParseError,
    Source,
)

_BUILTIN_PKG = "mewcode.subagent.builtin"


class Catalog:
    """name → 最高优先级定义的线程安全映射（spec F2.3）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._defs: dict[str, AgentDefinition] = {}
        self._by_source: dict[Source, list[AgentDefinition]] = {
            src: [] for src in Source
        }

    def resolve(self, name: str) -> AgentDefinition | None:
        """按名取最高优先级定义；不存在返回 None（spec F2.3）。"""
        with self._lock:
            return self._defs.get(name)

    def list(self) -> list[AgentDefinition]:
        """全部定义（按 name 升序，供 Agent 工具 description 渲染 / UI 列表）。"""
        with self._lock:
            return sorted(self._defs.values(), key=lambda d: d.name)

    def list_by_source(self, src: Source) -> list[AgentDefinition]:
        """某来源层的全部定义（/agents debug 用）。"""
        with self._lock:
            return list(self._by_source[src])

    def fork_definition(self) -> AgentDefinition:
        """Fork 路径伪定义（plan F6 设计）：name="__fork__"，正文继承父，强制后台。

        定义式与 Fork 走同一构造路径（spec F3）。
        """
        return AgentDefinition(
            name="__fork__",
            description="Fork-based subagent",
            model="inherit",
            max_turns=DEFAULT_MAX_TURNS,
            permission_mode=PermissionMode.DEFAULT,
            background=True,
            source=Source.BUILTIN,
        )

    def _add(self, definition: AgentDefinition) -> None:
        """写入定义（高优先级层先调，同名只保留首个——即最高优先级）。"""
        with self._lock:
            if definition.name in self._defs:
                return
            self._defs[definition.name] = definition
            self._by_source[definition.source].append(definition)


def load_catalog(project_root: str, agents_cfg: AgentConfig | None = None) -> Catalog:
    """四层加载（spec F2.2）：项目 → 用户 → 内置 →（插件跳过）。"""
    agents_cfg = agents_cfg or AgentConfig()
    catalog = Catalog()
    # 高优先级先写：项目 > 用户 > 内置（后扫到的同名不覆盖已有）
    _load_dir(catalog, Path(project_root) / ".mewcode" / "agents", Source.PROJECT)
    _load_dir(catalog, Path.home() / ".mewcode" / "agents", Source.USER)
    _load_builtin(catalog, agents_cfg)
    return catalog


def _load_dir(catalog: Catalog, directory: Path, source: Source) -> None:
    """加载一个目录下的 *.md（用户/项目级：失败 skip + stderr，spec F2.4）。"""
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.md")):
        try:
            definition = parse_definition(str(path), source)
        except DefinitionParseError as exc:
            print(f"subagent {exc.path}: {exc.reason}, skipped", file=sys.stderr)
            continue
        if definition.enabled:
            catalog._add(definition)


def _load_builtin(catalog: Catalog, agents_cfg: AgentConfig) -> None:
    """加载内置角色（随包发布，importlib.resources；失败 raise——代码 bug，N4 fail-fast）。

    verifier 默认 enabled:false，仅 agents.enable_verifier=True 时加载（spec F2.5）。
    """
    try:
        package = importlib.resources.files(_BUILTIN_PKG)
        files = sorted(f for f in package.iterdir() if f.name.endswith(".md"))
    except Exception as exc:
        # 打包缺失按代码 bug fail-fast（N4）
        raise RuntimeError(f"builtin subagent package unavailable: {exc}") from exc
    for file in files:
        data = file.read_bytes()
        # 内置解析失败直接 raise（fail-fast，N4；parse_builtin 抛 DefinitionParseError）
        definition = parse_builtin(data, file.name)
        # enabled:false 的角色（verifier）默认不加载；仅开关显式启用时加载（F2.5）
        if not definition.enabled and not (
            definition.name == "verifier" and agents_cfg.enable_verifier
        ):
            continue
        catalog._add(definition)


def parse_builtin(data: bytes, name: str) -> AgentDefinition:
    """解析内置角色字节内容（独立入口：不走磁盘路径，便于 importlib.resources）。"""
    raw = data.decode("utf-8")
    return parse_definition_text(raw, Source.BUILTIN, f"builtin:{name}")
