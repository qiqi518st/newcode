"""ActiveSkills 激活态单测（T23）：激活/失活/覆盖/预算淘汰/total_tokens。

防的 bug：enforce_budget 持锁调 total_tokens 死锁（须用 RLock）；重复激活改变顺序；
snapshot 浅拷贝导致外部改动污染内部；预算淘汰不按激活顺序（F8.1 踢最旧）。
"""

from newcode.skills.active import ActiveSkills


def test_activate_and_snapshot():
    store = ActiveSkills()
    store.activate("a", "body-a")
    store.activate("b", "body-b")
    assert store.names() == ["a", "b"]
    assert [e.name for e in store.snapshot()] == ["a", "b"]


def test_repeat_activate_overwrites_in_place():
    store = ActiveSkills()
    store.activate("a", "v1")
    store.activate("b", "v2")
    store.activate("a", "v3")
    assert store.names() == ["a", "b"]
    assert store.snapshot()[0].body == "v3"


def test_deactivate_and_clear():
    store = ActiveSkills()
    store.activate("a", "1")
    store.activate("b", "2")
    store.deactivate("a")
    assert store.names() == ["b"]
    store.deactivate("missing")  # 不抛
    store.clear()
    assert store.names() == []


def test_enforce_budget_evicts_oldest():
    """防 bug：超预算按激活顺序淘汰最旧（F8.1），不按字典序或随机。"""
    store = ActiveSkills()
    store.activate("oldest", "x" * 500)
    store.activate("middle", "y" * 500)
    store.activate("newest", "z" * 500)
    survivors = store.enforce_budget(300)
    assert [e.name for e in survivors] == ["middle", "newest"]
    assert store.names() == ["middle", "newest"]


def test_enforce_budget_within_budget_keeps_all():
    store = ActiveSkills()
    store.activate("a", "small")
    survivors = store.enforce_budget(10_000)
    assert [e.name for e in survivors] == ["a"]


def test_enforce_budget_empty_store():
    store = ActiveSkills()
    assert store.enforce_budget(100) == []
    assert store.names() == []


def test_total_tokens_accumulates():
    store = ActiveSkills()
    store.activate("a", "xx")
    store.activate("b", "yyyy")
    assert store.total_tokens(lambda text: len(text)) == 6


def test_snapshot_is_deep_copy():
    """防 bug：snapshot 返回深拷贝，外部改 body 不污染内部状态。"""
    store = ActiveSkills()
    store.activate("a", "body")
    snap = store.snapshot()
    snap[0].body = "mutated"
    assert store.snapshot()[0].body == "body"
