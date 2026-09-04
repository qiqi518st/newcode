"""自动路径连续失败闸（spec F28 防菜单轰炸；仅自动路径，不跨种类）。"""

from ..context.constants import AUTO_GATE_LIMIT


class AutoCompactGate:
    """仅自动路径的连续失败闸：连续 AUTO_GATE_LIMIT 轮自动压缩行动都失败后停自动触发；
    手动 /compact 成功一次即解除。手动 / 紧急路径不读写本类。

    无需显式锁——Python asyncio 单线程事件循环保证串行。
    """

    def __init__(self) -> None:
        self._consecutive_failures = 0

    def record_auto_success(self) -> None:
        """自动路径成功 → 计数清零（含闸外成功）。"""
        self._consecutive_failures = 0

    def record_auto_failure(self) -> None:
        """自动路径失败 → 计数 +1。"""
        self._consecutive_failures += 1

    def auto_disabled(self) -> bool:
        """自动触发是否已被闸停（连续失败达阈值）。"""
        return self._consecutive_failures >= AUTO_GATE_LIMIT

    def reset_on_manual_success(self) -> None:
        """手动 /compact 成功 → 清零解除闸。"""
        self._consecutive_failures = 0
