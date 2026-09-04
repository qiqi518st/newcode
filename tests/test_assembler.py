"""Prompt 组装管线测试（ch05，spec F2 / N8）"""

import logging

from newcode.prompt.assembler import PayloadAssembler, PromptPayload
from newcode.provider.base import Message, ToolDefinition


def _tool(name="t1"):
    return ToolDefinition(name=name, description="d", parameters={})


class TestThreeChannelRouting:
    """三通道分发：stable→段1、env→段2、history/reminders→messages、tools"""

    def test_routes_fields(self):
        asm = PayloadAssembler()
        payload = asm.assemble(
            stable_prompt="STABLE",
            env_segment="ENV",
            history=[Message(role="user", content="hi")],
            reminders=[
                Message(role="user", content="<system-reminder>r</system-reminder>")
            ],
            tools=[_tool()],
        )
        assert isinstance(payload, PromptPayload)
        assert payload.stable_prompt == "STABLE"
        assert payload.env_segment == "ENV"
        assert [m.content for m in payload.messages] == ["hi"]
        assert payload.reminders[0].content == "<system-reminder>r</system-reminder>"
        assert payload.tools[0].name == "t1"

    def test_history_and_reminders_kept_separate(self):
        """历史与轮次级 reminders 分开存放（reminders 不混入持久历史）"""
        asm = PayloadAssembler()
        payload = asm.assemble(
            stable_prompt="S",
            env_segment="E",
            history=[Message(role="assistant", content="a")],
            reminders=[
                Message(role="user", content="<system-reminder>x</system-reminder>")
            ],
            tools=None,
        )
        assert len(payload.messages) == 1
        assert len(payload.reminders) == 1
        assert payload.messages[0].role == "assistant"
        assert payload.reminders[0].role == "user"


class TestStableConsistency:
    """稳定前缀跨轮逐字节一致校验（N8）"""

    def test_same_stable_no_warning(self, caplog):
        asm = PayloadAssembler()
        asm.assemble("SAME", "E", [], [], None)
        with caplog.at_level(logging.WARNING, logger="newcode.prompt.assembler"):
            asm.assemble("SAME", "E", [], [], None)
        assert not [r for r in caplog.records if "跨轮变化" in r.getMessage()]

    def test_changed_stable_warns(self, caplog):
        asm = PayloadAssembler()
        asm.assemble("OLD", "E", [], [], None)
        with caplog.at_level(logging.WARNING, logger="newcode.prompt.assembler"):
            asm.assemble("NEW", "E", [], [], None)
        assert any("跨轮变化" in r.getMessage() for r in caplog.records)

    def test_first_call_no_warning(self, caplog):
        """首次调用（无基线）不告警"""
        asm = PayloadAssembler()
        with caplog.at_level(logging.WARNING, logger="newcode.prompt.assembler"):
            asm.assemble("FIRST", "E", [], [], None)
        assert not [r for r in caplog.records if "跨轮变化" in r.getMessage()]
