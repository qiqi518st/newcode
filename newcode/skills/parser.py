"""Skill 解析器：frontmatter + body 分离与校验、名字归一化。

两种磁盘布局（F1.1）：
- 单文件：`<skills_dir>/<name>.md`（文件本身即 frontmatter + body）。
- 目录型：`<skills_dir>/<name>/SKILL.md` + 可选 `tool.json` + `references/`。

对外接口：
- normalize_name(name) -> str：转小写、非字母数字转 `-`（F1.4）。
- parse_frontmatter_and_body(raw) -> tuple[dict, str]：`---\n...\n---\n` 分离。
- parse_skill(path, source) -> Skill：path 为 `.md` 文件（单文件）或含 SKILL.md 的目录。

失败策略（N3 失败隔离）：任何解析失败抛 SkillParseError，调用方（Catalog）捕获后
跳过该 Skill 并记 warning，不阻断整体加载。mode/context 取值非法时 warning 降级为
缺省值（mode→inline、context→none），不抛错（F1.2 补充说明）。
"""

import logging
import re
from pathlib import Path

import yaml

from .types import (
    Skill,
    SkillMeta,
    SkillParseError,
    SkillSource,
    ToolSchema,
)

logger = logging.getLogger(__name__)

# 名字合法性（归一化后）：小写字母开头，仅字母数字与 -（与 /名字 命令注册对齐）
_NAME_RE = re.compile(r"^[a-z][a-z0-9\-]*$")

_FRONTMATTER_OPEN = "---"
_FRONTMATTER_CLOSE = "---"


def normalize_name(name: str) -> str:
    """名字归一化：转小写、非字母数字转 `-`（F1.4）。"""
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).lower()
    return normalized.strip("-")


def _validate_name(name: str) -> None:
    """校验归一化后的名字合法性；非法抛 SkillParseError（F1.2/F1.5）。"""
    if not name:
        raise SkillParseError("skill name is empty")
    if not _NAME_RE.match(name):
        raise SkillParseError(
            f"invalid skill name after normalization: {name!r} "
            "(must match ^[a-z][a-z0-9-]*$)"
        )


def parse_frontmatter_and_body(raw: str) -> tuple[dict, str]:
    """分离 frontmatter 与正文。

    要求首行恰为 `---`；到下一个独立 `---` 行为界。frontmatter 缺失 / 未闭合 /
    非 YAML / 解析结果非 dict 均抛 SkillParseError。
    正文为 frontmatter 之后的内容（去除分隔线后到正文之间的空行）。
    """
    lines = raw.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_OPEN:
        raise SkillParseError("frontmatter missing: expected a leading '---' line")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_CLOSE:
            end = i
            break
    if end is None:
        raise SkillParseError("frontmatter unclosed: missing closing '---' line")
    fm_text = "\n".join(lines[1:end])
    try:
        meta = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise SkillParseError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillParseError(
            f"frontmatter must be a mapping, got {type(meta).__name__}"
        )
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body


def _validate_meta(meta: dict) -> SkillMeta:
    """从解析出的 frontmatter dict 构造 SkillMeta，做必填与枚举校验。

    - name / description 必填，缺失抛 SkillParseError（F1.2）。
    - mode / context 取值非法时 warning 降级为缺省（不抛错，F1.2 补充说明）。
    """
    name = str(meta.get("name", "")).strip()
    if not name:
        raise SkillParseError("missing required frontmatter field: name")
    normalized = normalize_name(name)
    _validate_name(normalized)

    description = meta.get("description", "")
    if not description or not str(description).strip():
        raise SkillParseError(
            f"skill {normalized!r}: missing required frontmatter field: description"
        )

    allowed = meta.get("allowedTools", [])
    if not isinstance(allowed, list):
        allowed = []
    allowed_tools = [str(t).strip() for t in allowed if str(t).strip()]

    mode_raw = str(meta.get("mode", "inline") or "inline").strip().lower()
    if mode_raw not in ("inline", "fork"):
        logger.warning(
            "skill %s: invalid mode %r, falling back to 'inline'", normalized, mode_raw
        )
        mode: str = "inline"
    else:
        mode = mode_raw

    ctx_raw = str(meta.get("context", "none") or "none").strip().lower()
    if ctx_raw not in ("none", "recent", "full"):
        logger.warning(
            "skill %s: invalid context %r, falling back to 'none'", normalized, ctx_raw
        )
        ctx: str = "none"
    else:
        ctx = ctx_raw
    # frontmatter 键名 context → 内部字段 fork_context（F1.2 补充说明）
    fork_context = ctx

    model = meta.get("model")
    model = str(model).strip() if model else None

    return SkillMeta(
        name=normalized,
        description=str(description).strip(),
        allowed_tools=allowed_tools,
        mode=mode,  # type: ignore[arg-type]
        fork_context=fork_context,  # type: ignore[arg-type]
        model=model,
    )


def _parse_tool_schema(name: str, data: dict) -> ToolSchema:
    """把 tool.json 单条条目转成 ToolSchema（F9.2）。

    entrypoint 必须存在且为 references/ 下的相对路径字符串；缺失抛 SkillParseError
    （tool.json 无效 → 该 Skill 移除，F3.8）。
    """
    schema_name = str(data.get("name", name)).strip()
    description = str(data.get("description", "")).strip()
    parameters = data.get("parameters", {})
    entrypoint = str(data.get("entrypoint", "")).strip()
    if not schema_name or not description or not isinstance(parameters, dict):
        raise SkillParseError(
            f"tool.json entry invalid (name/description/parameters required): {data!r}"
        )
    if not entrypoint or not entrypoint.startswith("references/"):
        raise SkillParseError(
            f"tool.json entry {schema_name!r}: entrypoint must be a 'references/...' path"
        )
    return ToolSchema(
        name=schema_name,
        description=description,
        parameters=parameters,
        entrypoint=entrypoint,
    )


def _read_tool_schemas(skill_dir, skill_name: str) -> tuple[ToolSchema, ...]:
    """目录型 Skill 读取 tool.json（可选，F9.1）；缺省无注册工具。

    tool.json 缺失 → 返回空元组（合法）；存在但非法 → 抛 SkillParseError（F3.8）。
    """
    tool_json = skill_dir / "tool.json"
    if not tool_json.is_file():
        return ()
    try:
        data = yaml.safe_load(tool_json.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SkillParseError(
            f"skill {skill_name}: tool.json unreadable/invalid: {exc}"
        ) from exc
    if data is None:
        return ()
    if not isinstance(data, dict):
        raise SkillParseError(
            f"skill {skill_name}: tool.json must be a mapping of tool name to schema"
        )
    entries = data.get("tools", data) if isinstance(data.get("tools"), list) else data
    schemas: list[ToolSchema] = []
    if isinstance(entries, list):
        for item in entries:
            if not isinstance(item, dict):
                raise SkillParseError(
                    f"skill {skill_name}: tool.json entry is not an object: {item!r}"
                )
            schemas.append(_parse_tool_schema(str(item.get("name", "")), item))
    else:
        for key, item in entries.items():
            if not isinstance(item, dict):
                raise SkillParseError(
                    f"skill {skill_name}: tool.json entry {key!r} is not an object"
                )
            schemas.append(_parse_tool_schema(str(key), item))
    return tuple(schemas)


def parse_skill(path, source: SkillSource) -> Skill:
    """解析一个 Skill 文件/目录为 Skill 对象。

    path 为 `.md` 文件（单文件布局，F1.1）或含 SKILL.md 的目录（目录型布局）。
    """
    p = path if isinstance(path, Path) else Path(str(path))
    if p.is_dir():
        skill_md = p / "SKILL.md"
        if not skill_md.is_file():
            raise SkillParseError(f"SKILL.md not found in directory {p}")
        source_path = skill_md
        source_dir = p
    else:
        if p.suffix.lower() != ".md":
            raise SkillParseError(f"single-file skill must be a .md file: {p}")
        source_path = p
        source_dir = p.parent
    try:
        raw = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillParseError(f"cannot read skill source {source_path}: {exc}") from exc
    meta_dict, body = parse_frontmatter_and_body(raw)
    meta = _validate_meta(meta_dict)
    tools = (
        _read_tool_schemas(source_dir, meta.name)
        if source_path.name == "SKILL.md"
        else ()
    )
    return Skill(
        meta=meta,
        prompt_body=body,
        source_dir=source_dir,
        source=source,
        tools=tools,
        source_path=source_path,
    )
