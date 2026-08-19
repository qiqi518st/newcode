"""AutoCompactGate 自动闸单测（ch08 T28，spec F28）。

防 bug：自动闸在错误次数触发/解除、跨种类计数污染。
"""

from mewcode.context.autogate import AutoCompactGate


def test_consecutive_failures_trips_at_3():
    """防 bug：连续失败未在第 3 轮触发闸 → 菜单轰炸。

    2 次失败未 disabled，第 3 次失败后 disabled。
    """
    gate = AutoCompactGate()
    gate.record_auto_failure()
    gate.record_auto_failure()
    assert not gate.auto_disabled(), "2 次失败不应闸停"
    gate.record_auto_failure()
    assert gate.auto_disabled(), "3 次失败应闸停"


def test_success_resets():
    """防 bug：自动成功未清零计数 → 闸永久卡住。

    失败 2 次后一次成功应清零。
    """
    gate = AutoCompactGate()
    gate.record_auto_failure()
    gate.record_auto_failure()
    gate.record_auto_success()
    assert not gate.auto_disabled()
    # 再失败 1 次不应闸停（已清零）
    gate.record_auto_failure()
    assert not gate.auto_disabled()


def test_manual_success_resets_gate():
    """防 bug：手动 /compact 成功未解除自动闸 → 用户手动压缩后仍不恢复自动。

    闸已 disabled 时 reset_on_manual_success 应恢复。
    """
    gate = AutoCompactGate()
    for _ in range(3):
        gate.record_auto_failure()
    assert gate.auto_disabled()
    gate.reset_on_manual_success()
    assert not gate.auto_disabled(), "手动成功应解除自动闸"


def test_no_cross_kind_methods():
    """防 bug：闸暴露了手动/紧急的计数方法 → 跨种类计数污染。

    AutoCompactGate 只应有自动路径方法 + 手动解除方法，无手动/紧急失败计数。
    """
    public = {m for m in dir(AutoCompactGate) if not m.startswith("_")}
    # 不应存在 record_manual_failure / record_force_failure 等跨种类方法
    forbidden = {"record_manual_failure", "record_force_failure", "record_emergency_failure"}
    assert not (public & forbidden), f"存在跨种类方法: {public & forbidden}"
