"""ContentReplacementState 账本单测（ch08 T28，spec F5）。

防 bug：决策翻转、落盘失败却记账、并发中间态、重造预览破坏缓存冻结。
"""

import pytest

from mewcode.context.replacement import ContentReplacementState


@pytest.mark.anyio
async def test_decide_once_replaced_freeze():
    """防 bug：replaced 决策后再评同 id 时重造预览字符串 → PromptCache 失效。

    首评 replaced → 再评应复用冻结的同一 preview 字符串，decide 回调不被二次调用。
    """
    state = ContentReplacementState()
    call_count = [0]

    def decide():
        call_count[0] += 1
        return ("replaced", "FROZEN_PREVIEW")

    first = state.decide_once("id1", "original", decide)
    assert first == "FROZEN_PREVIEW"
    assert call_count[0] == 1

    second = state.decide_once("id1", "original", decide)
    assert second == "FROZEN_PREVIEW"  # 复用冻结，不重造
    assert call_count[0] == 1  # decide 不被二次调用


@pytest.mark.anyio
async def test_decide_once_kept_freeze():
    """防 bug：kept 决策后再评时翻转成 replaced → 缓存失效。

    首评 kept → 再评返回原文，永不翻转。
    """
    state = ContentReplacementState()
    call_count = [0]

    def decide():
        call_count[0] += 1
        return ("kept", "")

    first = state.decide_once("id2", "ORIGINAL", decide)
    assert first == "ORIGINAL"
    assert call_count[0] == 1

    # 再评：即使 decide 想返回 replaced，也因已 seen 直接返回原文
    second = state.decide_once(
        "id2", "ORIGINAL", lambda: ("replaced", "SHOULD_NOT_WIN")
    )
    assert second == "ORIGINAL"
    assert call_count[0] == 1  # decide 仍不被调用


@pytest.mark.anyio
async def test_decide_once_skip_not_marked():
    """防 bug：落盘失败（skip）却记账 → 永不重试、内容永久丢失。

    skip → 账本不写，下轮重评时 decide 仍被调用（未被 seen 跳过）。
    """
    state = ContentReplacementState()
    total_calls = [0]

    def decide_skip():
        total_calls[0] += 1
        return ("skip", "")

    def decide_now():
        total_calls[0] += 1
        return ("replaced", "NOW_OK")

    first = state.decide_once("id3", "ORIGINAL", decide_skip)
    assert first == "ORIGINAL"  # skip 返回原文
    assert total_calls[0] == 1

    # 下轮重评：未记账，decide 仍被调用
    second = state.decide_once("id3", "ORIGINAL", decide_now)
    assert second == "NOW_OK"
    assert total_calls[0] == 2  # 两次都真正调用了 decide


@pytest.mark.anyio
async def test_decide_once_concurrent_atomic():
    """防 bug：并发同 id 时出现「已 Seen 但 replacement 未写」中间态。

    20 task 并发 decide_once 同 id → decide 回调恰好被调用一次（无中间态）。
    """
    import asyncio

    state = ContentReplacementState()
    call_count = [0]

    def decide():
        call_count[0] += 1
        return ("replaced", "CONCURRENT_PREVIEW")

    async def worker():
        # 模拟并发：每个 worker 都试图 decide_once 同一 id
        return state.decide_once("shared-id", "ORIG", decide)

    results = await asyncio.gather(*[worker() for _ in range(20)])
    # 所有结果要么是 CONCURRENT_PREVIEW（首评），要么是冻结复用
    assert all(r == "CONCURRENT_PREVIEW" for r in results), results
    assert call_count[0] == 1  # decide 恰好一次


def test_decision_for_unseen():
    """防 bug：未决策的 id 查询应返回 unseen，而非抛异常。"""
    state = ContentReplacementState()
    decision, preview = state.decision_for("never-seen")
    assert decision == "unseen"
    assert preview is None


def test_decision_for_replaced_returns_preview():
    """防 bug：replaced 的只读查询应返回冻结预览。"""
    state = ContentReplacementState()
    state.decide_once("id", "orig", lambda: ("replaced", "PREVIEW"))
    decision, preview = state.decision_for("id")
    assert decision == "replaced"
    assert preview == "PREVIEW"


def test_decision_for_kept_no_preview():
    """防 bug：kept 的只读查询不应返回预览（无预览）。"""
    state = ContentReplacementState()
    state.decide_once("id", "orig", lambda: ("kept", ""))
    decision, preview = state.decision_for("id")
    assert decision == "kept"
    assert preview is None
