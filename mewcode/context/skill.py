"""Skill 最小骨架（spec F31：为压缩后恢复的 Skill 注入留挂载点；内容加载留后续）。"""

from dataclasses import dataclass


@dataclass
class Skill:
    """Skill 最小结构。

    content 为占位——TODO(ch08): Skill 内容加载待后续章节实现，当前始终为空。
    """

    name: str
    description: str
    content: str = ""


class SkillRegistry:
    """Skill 注册与查询容器（骨架）。"""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """注册一个 Skill（按名覆盖）。"""
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        """按名查询，未注册返回 None。"""
        return self._skills.get(name)

    def list(self) -> list[Skill]:
        """列举全部 Skill。"""
        return list(self._skills.values())

    def total_tokens(self, estimator) -> int:
        """按估算器求总 token；当前内容为空，恒返回 0。"""
        return 0
