"""ActiveSkills 激活态单测（ch11 T16 迁移：原 ch08 SkillRegistry 骨架被 skills/active.py 取代）。

防 bug：激活/失活/clear 状态管理、重复激活覆盖原位置、total_tokens 估算、
enforce_budget 按激活顺序淘汰最旧（F8.1）、get 缺失返回 None（容器语义）。
"""

from newcode.skills.active import ActiveSkills


def test_activate_deactivate_clear():
    """防 bug：激活后可列举、失活移除、clear 清空。"""
    store = ActiveSkills()
    store.activate("code-review", "body A")
    store.activate("deploy", "body B")
    assert store.names() == ["code-review", "deploy"]
    store.deactivate("code-review")
    assert store.names() == ["deploy"]
    store.clear()
    assert store.names() == []


def test_repeat_activate_overwrites_in_place():
    """防 bug：重复激活同名 Skill 覆盖原位置 body，不改变激活顺序。"""
    store = ActiveSkills()
    store.activate("a", "body1")
    store.activate("b", "body2")
    store.activate("a", "body1-new")
    names = store.names()
    assert names == ["a", "b"]
    assert store.snapshot()[0].body == "body1-new"


def test_total_tokens_with_estimator():
    """防 bug：total_tokens 接受估算器并累加各 body（对齐 ch08 接口，N10）。"""
    store = ActiveSkills()
    store.activate("a", "xx")
    store.activate("b", "yyy")
    assert store.total_tokens(lambda text: len(text)) == 5


def test_enforce_budget_evicts_oldest():
    """防 bug：超预算时按激活顺序淘汰最旧（F8.1），幸存列表有序。"""
    store = ActiveSkills()
    store.activate("oldest", "x" * 500)  # ~125 tokens
    store.activate("middle", "y" * 500)
    store.activate("newest", "z" * 500)
    # 总 ~375 tokens，预算 300 → 踢掉 oldest，留 middle+newest
    survivors = store.enforce_budget(300)
    names = [e.name for e in survivors]
    assert names == ["middle", "newest"], names
    assert store.names() == ["middle", "newest"]


def test_enforce_budget_within_budget_no_eviction():
    """防 bug：总 token 在预算内时不做任何淘汰。"""
    store = ActiveSkills()
    store.activate("a", "small")
    store.activate("b", "tiny")
    survivors = store.enforce_budget(10_000)
    assert [e.name for e in survivors] == ["a", "b"]
    assert store.names() == ["a", "b"]


def test_snapshot_is_copy():
    """防 bug：snapshot 返回拷贝，外部改动不影响内部状态。"""
    store = ActiveSkills()
    store.activate("a", "body")
    snap = store.snapshot()
    snap[0].body = "mutated"
    assert store.snapshot()[0].body == "body"
