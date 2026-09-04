"""skills 包 → prompt 包瘦类型桥接（prompt 包零依赖 skills）。

catalog_to_prompt_items / active_to_prompt_entries 把 skills 内部对象转成
prompt.skills_block 定义的瘦 dataclass，供 agent 每轮 env 合成用。
"""

from __future__ import annotations

from ..prompt.skills_block import ActiveSkillEntry, SkillCatalogItem
from .active import ActiveSkills
from .catalog import Catalog


def catalog_to_prompt_items(catalog: Catalog) -> list[SkillCatalogItem]:
    """阶段一摘要：Catalog 的 (name, description) → 瘦条目列表（排除 disabled）。"""
    return [
        SkillCatalogItem(name=name, description=desc)
        for name, desc in catalog.get_catalog()
    ]


def active_to_prompt_entries(active: ActiveSkills) -> list[ActiveSkillEntry]:
    """阶段二激活段：ActiveSkills 快照 → 瘦条目列表。"""
    return [ActiveSkillEntry(name=e.name, body=e.body) for e in active.snapshot()]
