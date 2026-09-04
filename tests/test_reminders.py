"""system-reminder 与规划模式按轮注入测试（ch05，spec F5/F6）"""

from newcode.prompt.reminders import (
    PLAN_MODE_FULL,
    PLAN_MODE_LEAN,
    plan_mode_reminder,
    system_reminder,
)


class TestSystemReminder:
    """补充消息格式"""

    def test_role_is_user(self):
        assert system_reminder("x").role == "user"

    def test_wrapped_in_tags(self):
        m = system_reminder("hello")
        assert m.content == "<system-reminder>hello</system-reminder>"

    def test_not_added_to_history(self):
        """补充消息是独立 Message 对象，调用方负责不写入持久历史"""
        m = system_reminder("x")
        # 断言它不包含任何历史归属信息（无 tool_call_id 等配对字段）
        assert m.tool_call_id is None and m.tool_use_id is None and m.name is None


class TestPlanModeReminder:
    """规划模式注入频率：turn 0/5 完整，1-4 精简"""

    def test_turn_zero_full(self):
        assert PLAN_MODE_FULL in plan_mode_reminder(0).content

    def test_turn_five_full(self):
        assert PLAN_MODE_FULL in plan_mode_reminder(5).content

    def test_turn_ten_full(self):
        assert PLAN_MODE_FULL in plan_mode_reminder(10).content

    def test_middle_turns_lean(self):
        for t in (1, 2, 3, 4, 6, 7, 8, 9):
            m = plan_mode_reminder(t)
            assert PLAN_MODE_LEAN in m.content
            assert PLAN_MODE_FULL not in m.content

    def test_all_turns_wrapped_in_tag(self):
        for t in (0, 1, 5):
            assert plan_mode_reminder(t).content.startswith("<system-reminder>")
            assert plan_mode_reminder(t).content.endswith("</system-reminder>")
