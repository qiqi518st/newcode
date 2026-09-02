"""ch14 worktree/slug.py slug 校验与命名测试。

防的 bug：
- validate_slug 放过 `..` / `./x` / `a//b` 等路径遍历输入（LLM 注入会建到仓库外）
- flat_slug / branch_name 未扁平化导致 Git 分支 D/F 冲突（worktree-team/a vs worktree-team）
- 手动创建者误用 agent-/wf- 前缀被后台清理误删（is_auto_name 分类必须准确）
"""

from __future__ import annotations

import pytest

from mewcode.worktree.slug import (
    branch_name,
    flat_slug,
    is_auto_name,
    random_agent_name,
    validate_slug,
)

_VALID = ["alice", "team/alice", "team-refactor/alice", "v1.0", "a_b", "a.b/c-d"]
_INVALID = ["", "..", ".", "./x", "../etc", "a//b", "/x", "a/", "a b", "a;b", "a" * 65]


@pytest.mark.parametrize("name", _VALID)
def test_valid_slug(name):
    validate_slug(name)  # 不抛即通过


@pytest.mark.parametrize("name", _INVALID)
def test_invalid_slug_raises(name):
    with pytest.raises(ValueError):
        validate_slug(name)


def test_error_carries_reason():
    with pytest.raises(ValueError, match="路径|段|长度|开头|//|空"):
        validate_slug("")
    with pytest.raises(ValueError, match=".."):
        validate_slug("../etc")


def test_flat_slug_and_branch_name():
    assert flat_slug("team-refactor/alice") == "team-refactor+alice"
    assert branch_name("team-refactor/alice") == "worktree-team-refactor+alice"
    assert branch_name("alice") == "worktree-alice"


def test_is_auto_name():
    assert is_auto_name("agent-a1b2c3de")  # agent-a + 7 位 hex
    assert is_auto_name("wf-task1")
    assert not is_auto_name("alice")
    assert not is_auto_name("agent-a1b2c3")  # 仅 6 位 hex 不匹配
    assert not is_auto_name("agent-aZZZZZZZ")  # 非 hex


def test_random_agent_name_format():
    for _ in range(10):
        name = random_agent_name()
        assert is_auto_name(name)
        assert name.startswith("agent-a")
        assert len(name) == len("agent-a") + 7
