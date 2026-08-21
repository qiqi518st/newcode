"""mewcode.skills —— Skill 技能包系统（ch11）。

提供 Skill 全生命周期：解析 / 加载 / 执行 / 激活态 / 预算淘汰。
对外导出核心类型与门面；Agent 只依赖 ActiveSkills，context 包经 ActiveSkills
挂钩压缩预算淘汰，skills 包不反向依赖 agent（避免循环依赖）。
"""

from .active import ActiveSkills
from .catalog import Catalog
from .executor import Executor
from .types import Skill, SkillMeta, SkillSource

__all__ = [
    "ActiveSkills",
    "Catalog",
    "Executor",
    "Skill",
    "SkillMeta",
    "SkillSource",
]
