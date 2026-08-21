"""T0a/T0b：permission count/add/reset API + ContextManager.reset_for_new_session。

防的 bug：
- add_rule 写错层（应写 local）/ 与 persist_local_allow 写回路径不一致导致规则不生效；
- reset_rules 误删 defaultMode 等其它配置；文件不存在时误建空文件；
- count_rules 读到未刷新内存层（应基于 live RuleLayers，非重新读文件）；
- reset_for_new_session 未清 L1 替换账本/自动闸/锚点，或未把 _conv 指向新会话（/clear 后压缩操作旧会话）。
"""

import os

from mewcode.context.autogate import AutoCompactGate
from mewcode.context.manager import ContextManager
from mewcode.context.replacement import ContentReplacementState
from mewcode.permission.checker import PermissionChecker
from mewcode.permission.modes import PermissionMode
from mewcode.permission.rules import RULE_FILE_LOCAL, RuleLayers


def _checker(tmp: str) -> PermissionChecker:
    return PermissionChecker(
        project_root=tmp, mode=PermissionMode.DEFAULT, layers=RuleLayers()
    )


def test_count_rules_initial_zero(tmp_path):
    checker = _checker(str(tmp_path))
    assert checker.count_rules() == 0


def test_add_rule_then_count_increments(tmp_path):
    checker = _checker(str(tmp_path))
    checker.add_rule("Bash(git *)", "allow")
    assert checker.count_rules() == 1
    checker.add_rule("Read", "deny")
    assert checker.count_rules() == 2
    # 落盘到本地规则文件（复用 persist_local_allow 写回路径）
    local = os.path.join(str(tmp_path), RULE_FILE_LOCAL)
    assert os.path.exists(local)
    with open(local, encoding="utf-8") as f:
        assert "Bash(git *)" in f.read()


def test_add_rule_dedupe(tmp_path):
    checker = _checker(str(tmp_path))
    checker.add_rule("Bash(git *)", "allow")
    checker.add_rule("Bash(git *)", "allow")
    assert checker.count_rules() == 1


def test_reset_rules_returns_count_and_keeps_other_settings(tmp_path):
    tmp = str(tmp_path)
    checker = _checker(tmp)
    checker.add_rule("Bash(git *)", "allow")
    checker.add_rule("Read", "deny")
    local = os.path.join(tmp, RULE_FILE_LOCAL)
    os.makedirs(os.path.dirname(local), exist_ok=True)
    with open(local, "w", encoding="utf-8") as f:
        f.write(
            "defaultMode: acceptEdits\npermissions:\n  allow:\n    - 'Bash(git *)'\n"
        )
    # 重建 checker 以读取该文件（与真实启动路径一致）
    reloaded = PermissionChecker.create(tmp)
    assert reloaded.count_rules() == 1
    removed = reloaded.reset_rules()
    assert removed == 1
    assert reloaded.count_rules() == 0
    # defaultMode 保留，未被 reset 误删
    with open(local, encoding="utf-8") as f:
        assert "acceptEdits" in f.read()


def test_reset_rules_missing_file_returns_zero(tmp_path):
    checker = _checker(str(tmp_path))
    assert checker.reset_rules() == 0


def test_reset_for_new_session_clears_state_and_repoints_conv():
    cm = object.__new__(ContextManager)
    cm._state = ContentReplacementState()
    cm._state._seen_ids.add("x")
    cm._state._replacements["x"] = "preview"
    cm._auto_gate = AutoCompactGate()
    for _ in range(5):
        cm._auto_gate.record_auto_failure()  # 触发闸停
    cm._usage_anchor = 999
    cm._anchor_msg_len = 88
    old_conv = object()
    cm._conv = old_conv

    new_conv = object()
    cm.reset_for_new_session(new_conv)

    assert cm._usage_anchor == 0
    assert cm._anchor_msg_len == 0
    assert not cm._auto_gate.auto_disabled()  # 闸计数归零
    assert cm._state.decision_for("x") == ("unseen", None)  # 账本清空
    assert cm._conv is new_conv  # 指向新会话
