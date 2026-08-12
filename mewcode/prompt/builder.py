"""系统提示模块组装器：Section 结构体 + PromptBuilder"""

from dataclasses import dataclass


@dataclass
class Section:
    """系统提示模块：一段独立的指令片段"""

    name: str  # 模块名，如 "identity"、"behavior"
    content: str  # 模块指令文本
    priority: int  # 优先级，数字越小越靠前（固定模块 1-7，可选模块 10+）


class PromptBuilder:
    """按优先级拼装多个 Section，产出稳定系统提示（跨轮不变）"""

    def __init__(self, sections: list[Section] | None = None) -> None:
        self._sections: list[Section] = list(sections or [])

    def add(self, section: Section) -> None:
        """注册一个模块"""
        self._sections.append(section)

    def build(self) -> str:
        """按 priority 升序拼装，模块间空行分隔；同优先级按注册顺序（sorted 稳定）"""
        ordered = sorted(self._sections, key=lambda s: s.priority)
        return "\n\n".join(s.content for s in ordered)
