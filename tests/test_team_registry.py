"""AgentNameRegistry 测试（ch15 F9）。

防的 bug：
- 同名覆盖后旧 agent_id 反向映射残留（F9.4）
- 同 agent_id 换名后旧 name 残留
- resolve 给 agent_id 时应返回 agent_id 本身（SendMessage 目标寻址）
"""

from __future__ import annotations

from mewcode.team.registry import AgentNameRegistry


def test_register_resolve_name_of():
    r = AgentNameRegistry()
    r.register("alice", "agent-123")
    assert r.resolve("alice") == "agent-123"
    assert r.resolve("agent-123") == "agent-123"  # id 直查原样
    assert r.name_of("agent-123") == "alice"


def test_name_overwrite_cleans_old_id():
    # 防的 bug：alice 覆盖后 agent-123 仍映射到 alice（F9.4 弱引用覆盖）
    r = AgentNameRegistry()
    r.register("alice", "agent-123")
    r.register("alice", "agent-456")
    assert r.resolve("alice") == "agent-456"
    assert r.name_of("agent-123") is None


def test_same_id_rename_cleans_old_name():
    r = AgentNameRegistry()
    r.register("alice", "agent-456")
    r.register("bob", "agent-456")
    assert r.name_of("agent-456") == "bob"
    assert r.resolve("alice") is None


def test_unregister_and_list():
    r = AgentNameRegistry()
    r.register("a", "id1")
    r.register("b", "id2")
    r.unregister("a")
    assert r.resolve("a") is None
    r.unregister_by_agent_id("id2")
    assert r.resolve("b") is None
    assert r.list_() == {}


def test_resolve_unknown_none():
    assert AgentNameRegistry().resolve("nobody") is None
