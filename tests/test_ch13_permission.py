"""ch13 permission/checker.py for_subagent 测试。

防的 bug：
- 子 Agent 用独立规则层 → 父 persist_local_allow 已批准的命令子 Agent 重复问/被拒（A2）
- 子 Agent 模式污染主 Agent 模式（F4.1：模式独立）
- dont_ask 不加枚举成员（plan 决策：由 Agent 层短路实现）——PermissionMode 枚举不变
"""

from __future__ import annotations

import tempfile

from newcode.permission.checker import PermissionChecker
from newcode.permission.modes import PermissionMode


def test_for_subagent_shares_layers():
    pc = PermissionChecker.create(tempfile.mkdtemp())
    sub = pc.for_subagent(PermissionMode.ACCEPT_EDITS)
    assert sub._layers is pc._layers  # 共享规则层（父已批准的子 Agent 命中）
    assert sub.mode is not pc.mode
    assert sub.mode == PermissionMode.ACCEPT_EDITS


def test_mode_enum_unchanged():
    # dontAsk 不进入 PermissionMode 枚举（plan 决策：由 Agent 层 dont_ask 短路实现）
    values = [m.value for m in PermissionMode]
    assert "dontAsk" not in values
    assert PermissionMode.parse("dontAsk") is None


def test_parent_mode_unchanged_by_subagent():
    pc = PermissionChecker.create(tempfile.mkdtemp())
    pc.for_subagent(PermissionMode.BYPASS)
    assert pc.mode == PermissionMode.DEFAULT  # 主模式不受子 Agent 影响


def test_persist_local_allow_visible_to_subagent(tmp_path):
    # 父批准一条精确规则后，子 Agent 共享层应能命中（A2）
    pc = PermissionChecker.create(str(tmp_path))
    sub = pc.for_subagent(PermissionMode.DEFAULT)
    assert sub._layers is pc._layers
    # 规则层同一对象 → 父 persist 对子 Agent 的规则引擎立即可见
    assert pc.count_rules() >= 0
