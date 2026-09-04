"""L3 规则引擎单元测试

背景：RuleEngine 把 RuleLayers 三层匹配结果转换为 CheckResult（deny 带原因）。
这些测试防的 bug：命中 deny 无原因回灌、命中 allow 误带 deny 原因、未命中返回
非 None 导致跳过模式矩阵。
"""

from newcode.permission.engine import RuleEngine
from newcode.permission.rules import Rule, RuleLayers
from newcode.permission.types import Decision


class TestRuleEngine:
    def test_deny_with_reason(self):
        layers = RuleLayers()
        layers.project.deny.append(Rule("Bash", "git *", "deny", "project"))
        engine = RuleEngine(layers)
        res = engine.match("Bash", "git push")
        assert res is not None
        assert res.decision == Decision.DENY
        # 拒绝原因应包含工具和 target，供模型调整策略（F8）
        assert "Bash" in res.reason
        assert "git push" in res.reason

    def test_allow_empty_reason(self):
        layers = RuleLayers()
        layers.local.allow.append(Rule("Read", "*.py", "allow", "local"))
        engine = RuleEngine(layers)
        res = engine.match("Read", "main.py")
        assert res is not None
        assert res.decision == Decision.ALLOW
        assert res.reason == ""

    def test_no_match_returns_none(self):
        engine = RuleEngine(RuleLayers())
        assert engine.match("Bash", "anything") is None

    def test_priority_chain(self):
        # 本地 deny > 项目 allow > 用户 deny，跨层首命中定案
        layers = RuleLayers()
        layers.user.deny.append(Rule("Write", "secret/**", "deny", "user"))
        layers.project.allow.append(Rule("Write", "src/**", "allow", "project"))
        layers.local.deny.append(Rule("Write", "src/secret/**", "deny", "local"))
        engine = RuleEngine(layers)

        assert engine.match("Write", "src/secret/a.txt").decision == Decision.DENY
        assert engine.match("Write", "src/ok.py").decision == Decision.ALLOW
        # 未命中任何层 → None
        assert engine.match("Write", "other/x.txt") is None
