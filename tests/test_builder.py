"""PromptBuilder 拼装测试（ch05）"""

from newcode.prompt.builder import PromptBuilder, Section
from newcode.prompt.sections import fixed_sections, optional_sections


class TestBuildOrdering:
    """优先级拼装与注册顺序"""

    def test_sort_by_priority(self):
        """乱序输入，按 priority 升序输出"""
        b = PromptBuilder()
        b.add(Section("low", "LOW", 7))
        b.add(Section("high", "HIGH", 1))
        b.add(Section("mid", "MID", 4))
        out = b.build()
        assert out.index("HIGH") < out.index("MID") < out.index("LOW")

    def test_same_priority_keeps_registration_order(self):
        """同优先级按注册顺序（sorted 稳定性）"""
        b = PromptBuilder()
        b.add(Section("a", "first", 3))
        b.add(Section("b", "second", 3))
        out = b.build()
        assert out.index("first") < out.index("second")

    def test_blank_line_separator(self):
        """模块间以空行分隔"""
        b = PromptBuilder([Section("a", "A", 1), Section("b", "B", 2)])
        assert b.build() == "A\n\nB"


class TestFixedSections:
    """七个固定模块定义（spec F1 顺序）"""

    def test_seven_modules_in_priority_order(self):
        """返回 7 个模块，priority 依次 1-7，顺序与 spec F1 一致"""
        fs = fixed_sections()
        assert len(fs) == 7
        assert [s.priority for s in fs] == [1, 2, 3, 4, 5, 6, 7]
        assert [s.name for s in fs] == [
            "identity",
            "behavior",
            "tool_usage",
            "code_quality",
            "security",
            "task_pattern",
            "output_style",
        ]

    def test_all_sections_nonempty(self):
        """每个模块内容非空"""
        for s in fixed_sections():
            assert s.content.strip(), f"模块 {s.name} 内容为空"


class TestOptionalSections:
    """可选模块：自定义指令追加语义"""

    def test_empty_prompt_returns_none(self):
        assert optional_sections("") == []

    def test_blank_prompt_returns_none(self):
        assert optional_sections("   ") == []

    def test_custom_prompt_appends_after_fixed(self):
        """自定义指令 priority=10，排在固定模块之后（追加语义）"""
        opts = optional_sections("我的自定义指令")
        assert len(opts) == 1
        assert opts[0].name == "custom_instruction"
        assert opts[0].priority == 10
        # 与固定模块合拼后，自定义指令出现在最后
        b = PromptBuilder(fixed_sections() + optional_sections("我的自定义指令"))
        out = b.build()
        assert "我的自定义指令" in out
        assert out.endswith("我的自定义指令")
