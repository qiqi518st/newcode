"""规则引擎（L3）

对一次工具调用匹配三层规则，返回 allow/deny/None。
"""

from .rules import RuleLayers
from .types import CheckResult, Decision


class RuleEngine:
    """规则引擎：跨层匹配三层规则"""

    def __init__(self, layers: RuleLayers) -> None:
        self._layers = layers

    def match(self, friendly: str, target: str) -> CheckResult | None:
        """跨层查找：local → project → user，首命中即返回。

        返回 None 表示未命中。
        """
        result = self._layers.match(friendly, target)
        if result is None:
            return None
        if result == Decision.DENY:
            return CheckResult(
                decision=Decision.DENY,
                reason=f"匹配 deny 规则：{friendly}({target})",
            )
        return CheckResult(decision=Decision.ALLOW, reason="")
