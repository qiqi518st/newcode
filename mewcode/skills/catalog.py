"""Skill 加载器：三级路径扫描与覆盖管理 + 热加载容错 + disabled 持久。

- 三级搜索路径（F2.1）：内置 <mewcode/skills/builtin/> → 用户 ~/.mewcode/skills/ →
  项目 <work_dir>/.mewcode/skills/，后扫同名覆盖前者（项目级 > 用户级 > 内置级）。
- 单文件 `*.md` 与目录型 `<name>/SKILL.md` 两种布局（F1.1）统一处理。
- get(name) 每次调用重读源文件（N7 热更新）；解析失败回退内存 _cache 旧版并记 warning
  （F2.3：内存 _cache = 最近一次成功解析的 Skill 对象，不落盘）。
- validate_tools(registry)：allowedTools 引用主环境不存在工具的 Skill 名返回（F2.7，
  启动期调用方 warning + 从 catalog 移除）。
- disabled 集合读写 ~/.mewcode/skills/disabled.json（F7.8 跨会话持久）。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from ..skills.constants import (
    BUILTIN_SKILLS_DIR,
    PROJECT_SKILLS_DIR,
    USER_SKILLS_DIR,
)
from ..skills.parser import parse_skill
from ..skills.script_tool import ScriptTool
from ..skills.types import Skill, SkillParseError, SkillSource

logger = logging.getLogger(__name__)


def register_skill_tools(registry, skill) -> None:
    """目录型 Skill：tool.json 声明的工具注册进主注册表（ScriptTool 子进程壳，F9.2/F9.6）。

    已存在同名工具跳过（F9.3 禁止重复定义内置工具）。executor 与 LoadSkillTool 共用。
    """
    for schema in skill.tools:
        if registry.get(schema.name) is not None:
            logger.warning(
                "skill %s: tool %s already registered, skipping duplicate",
                skill.name,
                schema.name,
            )
            continue
        registry.register(ScriptTool(schema, skill.source_dir))


def _scan_directory(skills_dir: Path, source: SkillSource) -> dict[str, Skill]:
    """扫描一个技能目录：`*.md` 单文件 + `<name>/SKILL.md` 目录型两种布局。

    单文件布局要求文件名与归一化名字一致（`<name>.md`，F1.1）；文件名与 frontmatter
    name 不一致时以文件名归一化结果作为注册名（F1.4 对齐 /名字 注册）。
    解析失败的单个文件跳过并记 warning，不阻断整体加载（N3）。
    """
    result: dict[str, Skill] = {}
    if not skills_dir.is_dir():
        return result
    # 目录型：<name>/SKILL.md（先扫，目录型自带 name 约束）
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue  # 非 Skill 目录，忽略
        try:
            skill = parse_skill(child, source)
        except SkillParseError as exc:
            logger.warning("skip skill directory %s: %s", child, exc)
            continue
        result[skill.name] = skill
    # 单文件：<name>.md
    for md in sorted(skills_dir.glob("*.md")):
        if md.name == "SKILL.md":
            continue  # 已被目录型分支处理（理论不会出现）
        try:
            skill = parse_skill(md, source)
        except SkillParseError as exc:
            logger.warning("skip skill file %s: %s", md, exc)
            continue
        # 单文件布局以文件名归一化为注册名（对齐 F1.4）；frontmatter name 非法已由 parse 抛错
        result[skill.name] = skill
    return result


class Catalog:
    """Skill 目录：三级扫描 + 覆盖 + 热加载 + 校验 + disabled 管理。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_name: dict[str, Skill] = {}
        self._order: list[str] = []  # 加载顺序（覆盖时更新位置）
        self._cache: dict[str, Skill] = {}  # 最近一次成功解析的 Skill（F2.3 回退用）
        self._disabled: set[str] = set()
        self._disabled_file: Path | None = None
        self._scan_roots: list[tuple[Path, SkillSource]] = []

    # ── 装载 ─────────────────────────────────────────────
    @classmethod
    def load(
        cls,
        project_dir: Path | None = None,
        user_skills_dir: Path | None = None,
        builtin_dir: Path | None = None,
    ) -> Catalog:
        """构造并按 内置→用户→项目 顺序扫描（后扫同名覆盖前者，F2.1）。

        参数可注入便于测试；缺省取真实路径。
        """
        c = cls()
        c._disabled_file = _resolve_disabled_file(user_skills_dir)
        c._load_disabled()
        roots: list[tuple[Path, SkillSource]] = [
            (Path(builtin_dir or BUILTIN_SKILLS_DIR), SkillSource.BUILTIN),
            (_expand_user(user_skills_dir or USER_SKILLS_DIR), SkillSource.USER),
            (
                _expand_project(project_dir) if project_dir else _cwd_project_skills(),
                SkillSource.PROJECT,
            ),
        ]
        c._scan_roots = roots
        c._scan_all()
        return c

    def _scan_all(self) -> None:
        """重扫全部根（启动 / reload 全量）。"""
        with self._lock:
            merged: dict[str, Skill] = {}
            for root, source in self._scan_roots:
                found = _scan_directory(root, source)
                for name, skill in found.items():
                    merged[name] = skill
            self._by_name = merged
            self._order = list(merged.keys())
            # 预热内存缓存：启动时全部成功解析的 Skill 进 _cache
            self._cache = dict(merged)

    def reload(self, work_dir: Path | None = None) -> tuple[list[str], list[str]]:
        """全量重扫，返回 (added, removed)（F2.6 / F7.3，调用方同步 slash 命令）。

        added 是本次扫描后有、上次没有的名字；removed 反之。
        """
        with self._lock:
            previous = set(self._by_name.keys())
            if work_dir is not None:
                # 项目根可能变化（如 /skill reload 在别的工作目录）
                self._scan_roots[-1] = (_expand_project(work_dir), SkillSource.PROJECT)
            self._scan_all()
            current = set(self._by_name.keys())
            return (
                sorted(current - previous),
                sorted(previous - current),
            )

    # ── 查询 ─────────────────────────────────────────────
    def get(self, name: str) -> Skill | None:
        """按名查询（含 disabled 状态外的所有 Skill）。

        每次调用重读源文件（N7 热更新）；解析失败回退内存 _cache 旧版并记 warning（F2.3）。
        """
        with self._lock:
            cached = self._by_name.get(name)
            if cached is None:
                return None
            try:
                fresh = parse_skill(cached.source_path, cached.source)
                # 磁盘新版本覆盖内存对象（保持 tools/source 同步）
                self._by_name[name] = fresh
                self._cache[name] = fresh
                return fresh
            except SkillParseError as exc:
                fallback = self._cache.get(name)
                logger.warning(
                    "skill %s: re-read failed (%s), falling back to cached version",
                    name,
                    exc,
                )
                if fallback is not None:
                    return fallback
                # 连缓存也没有 → 保持旧对象（防御性）
                return cached

    def list(self) -> list[Skill]:
        """列举全部 Skill（排除 disabled，F2.6/F7.1）。"""
        with self._lock:
            return [
                self._by_name[n]
                for n in self._order
                if n in self._by_name and not self._is_disabled_locked(n)
            ]

    def names(self) -> list[str]:
        """全部可用（非 disabled）Skill 名，按加载顺序。"""
        with self._lock:
            return [
                n
                for n in self._order
                if n in self._by_name and not self._is_disabled_locked(n)
            ]

    def get_catalog(self) -> list[tuple[str, str]]:
        """[(name, description), ...]（排除 disabled，F2.6/阶段一摘要素材）。

        文本拼接上移到装配层（reference：结构化列表 + 上移拼接）。
        """
        return [(s.name, s.meta.description) for s in self.list()]

    def get_source_label(self, name: str) -> str:
        """来源标签 project | user | builtin（F2.6 / /skill list）。"""
        with self._lock:
            skill = self._by_name.get(name)
            if skill is None:
                return ""
            return skill.source.value

    # ── 校验（F2.7 / B 决策：启动时剔除）────────────────────
    def validate_tools(self, registry) -> list[str]:
        """遍历所有 Skill 的 allowedTools，返回引用主环境不存在工具的 Skill 名列表。

        不修改状态——调用方（main.py 装配 / /skill reload）负责 warning + 从 catalog 移除
        （B 决策：不阻断其它 Skill 加载）。
        """
        bad: list[str] = []
        with self._lock:
            for name in self._order:
                skill = self._by_name.get(name)
                if skill is None:
                    continue
                for tool_name in skill.meta.allowed_tools:
                    if registry.get(tool_name) is None:
                        bad.append(name)
                        break
        return bad

    def remove(self, name: str) -> None:
        """从 catalog 移除（fail-fast 剔除 / /skill unload 用）。"""
        with self._lock:
            self._by_name.pop(name, None)
            self._cache.pop(name, None)
            if name in self._order:
                self._order.remove(name)

    # ── disabled 持久（F7.8）──────────────────────────────
    def is_disabled(self, name: str) -> bool:
        with self._lock:
            return self._is_disabled_locked(name)

    def _is_disabled_locked(self, name: str) -> bool:
        return name in self._disabled

    def set_disabled(self, name: str, disabled: bool) -> None:
        """启用/禁用；立即落盘 disabled.json（F7.8 跨会话持久）。"""
        with self._lock:
            if disabled:
                self._disabled.add(name)
            else:
                self._disabled.discard(name)
            self._write_disabled_locked()

    def _load_disabled(self) -> None:
        if self._disabled_file is None or not self._disabled_file.is_file():
            return
        try:
            data = json.loads(self._disabled_file.read_text(encoding="utf-8"))
            names = data if isinstance(data, list) else data.get("disabled", [])
            self._disabled = {str(n) for n in names if isinstance(n, str)}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "disabled state file unreadable (%s): %s", self._disabled_file, exc
            )

    def _write_disabled_locked(self) -> None:
        if self._disabled_file is None:
            return
        try:
            self._disabled_file.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(sorted(self._disabled), ensure_ascii=False, indent=2)
            self._disabled_file.write_text(payload, encoding="utf-8")
        except OSError as exc:
            logger.warning("cannot persist disabled state: %s", exc)


def _expand_user(path: Path) -> Path:
    return path.expanduser()


def _expand_project(path: Path) -> Path:
    return (path / PROJECT_SKILLS_DIR).resolve()


def _cwd_project_skills() -> Path:
    import os

    return (Path(os.getcwd()) / PROJECT_SKILLS_DIR).resolve()


def _resolve_disabled_file(user_skills_dir: Path | None) -> Path:
    """disabled.json 落盘位置：用户技能目录下的 disabled.json（F7.8）。"""
    base = _expand_user(user_skills_dir or USER_SKILLS_DIR)
    return base / "disabled.json"
