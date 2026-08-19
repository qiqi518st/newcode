"""Skill / SkillRegistry 骨架单测（ch08 T33，spec F31，AC26）。

防 bug：注册/查询/列举、total_tokens 空内容返回 0、缺失查询返回 None。
"""

from mewcode.context.skill import Skill, SkillRegistry


def test_register_get_list():
    """AC26：register 后可 get/list。"""
    reg = SkillRegistry()
    s1 = Skill(name="code-review", description="代码审查")
    s2 = Skill(name="deploy", description="部署")
    reg.register(s1)
    reg.register(s2)
    assert reg.get("code-review") is s1
    assert reg.get("deploy") is s2
    names = sorted(s.name for s in reg.list())
    assert names == ["code-review", "deploy"]


def test_total_tokens_zero():
    """AC26：内容加载未实现，total_tokens 始终返回 0。"""
    reg = SkillRegistry()
    reg.register(Skill(name="x", description="d"))
    reg.register(Skill(name="y", description="d"))
    # total_tokens 接受任意 estimator（当前骨架忽略，返回 0）
    assert reg.total_tokens(lambda msgs: 999) == 0


def test_get_missing_returns_none():
    """防 bug：get 未注册的 skill 应返回 None 而非抛 KeyError。"""
    reg = SkillRegistry()
    assert reg.get("nonexistent") is None


def test_skill_content_empty_by_default():
    """AC26：Skill.content 默认空（内容加载 TODO，F31）。"""
    s = Skill(name="x", description="d")
    assert s.content == ""
