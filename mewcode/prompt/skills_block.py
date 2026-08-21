"""env 段的 Skill 渲染（F4.1/F5.2）。

prompt 包不依赖 skills 包（避免循环依赖）：经瘦 dataclass 输入，
skills 包通过 adapter 把内部类型转成这里的瘦类型。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SkillCatalogItem:
    """阶段一摘要条目（name + 一句话说明）。"""

    name: str
    description: str


@dataclass
class ActiveSkillEntry:
    """激活 Skill 条目（name + 完整 SOP body）。"""

    name: str
    body: str


_LOAD_SKILL_GUIDE = (
    "If the user's request matches a Skill, call load_skill to activate it."
)


def render_skills_catalog(items: list[SkillCatalogItem]) -> str:
    """「Available Skills」摘要段（F4.1）：`- name: description` 列表 + load_skill 指引。

    空列表返回空串（装配层跳过该段，向后兼容 N10）。
    """
    if not items:
        return ""
    lines = ["## Available Skills", ""]
    lines.extend(f"- {it.name}: {it.description}" for it in items)
    lines.append("")
    lines.append(_LOAD_SKILL_GUIDE)
    return "\n".join(lines)


def render_active_skills_block(entries: list[ActiveSkillEntry]) -> str:
    """「## Active Skills」段：逐条 `### Skill: <name>` + 完整 SOP body（F5.2）。

    空列表返回空串。
    """
    if not entries:
        return ""
    blocks: list[str] = ["## Active Skills"]
    for e in entries:
        blocks.append("")
        blocks.append(f"### Skill: {e.name}")
        blocks.append(e.body)
    return "\n".join(blocks)
