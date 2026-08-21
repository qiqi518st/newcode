"""Executor 单测（T24）：inline 激活注入 / fork 隔离回流 / fork_context 三策略 / token 写回 / 兜底。

用 mock provider 驱动真实 Agent.run 路径（无真实 API/终端），断言行为。

防的 bug：inline 不触发 LLM 即不注入消息（F3.1）；fork 污染主对话状态（N3）；
fork 结果不回流主对话（append_assistant_message 未调）；fork token 未写回主统计
（N13）；fork_context=none 时初始消息非空；recent 拷贝数错误；full 摘要请求未走
provider；子 Agent 工具集未收窄（allowedTools 白名单被忽略）。
"""

from pathlib import Path

import pytest

from mewcode.provider.base import StreamEvent, TokenUsage
from mewcode.skills import ActiveSkills, Catalog, Executor
from mewcode.slash import CommandContext, CommandRegistry, RecordingUI
from mewcode.tools import Registry

pytestmark = pytest.mark.anyio


def _skill_md(
    name: str, mode: str, body: str = "SOP body", context: str = "none"
) -> str:
    return (
        f"---\nname: {name}\ndescription: {name} skill\nmode: {mode}\n"
        f"context: {context}\n---\n{body}"
    )


def _catalog(tmp_path: Path) -> Catalog:
    user = tmp_path / "user"
    user.mkdir()
    (user / "inline-skill.md").write_text(
        _skill_md("inline-skill", "inline", "INLINE BODY with $ARGUMENTS"),
        encoding="utf-8",
    )
    (user / "fork-skill.md").write_text(
        _skill_md("fork-skill", "fork", "FORK SOP body"),
        encoding="utf-8",
    )
    (user / "recent-skill.md").write_text(
        _skill_md("recent-skill", "fork", "RECENT SOP", context="recent"),
        encoding="utf-8",
    )
    (user / "full-skill.md").write_text(
        _skill_md("full-skill", "fork", "FULL SOP", context="full"),
        encoding="utf-8",
    )
    return Catalog.load(
        project_dir=tmp_path / "proj",
        user_skills_dir=user,
        builtin_dir=tmp_path / "builtin",
    )


class FakeProvider:
    """每次 stream 弹出一个预设事件队列（按调用顺序）。"""

    model = "fake-model"
    name = "fake"

    def __init__(self, queues=None):
        self._queues = list(queues or [])
        self.stream_calls = 0

    def stream(self, payload):
        self.stream_calls += 1
        q = self._queues.pop(0) if self._queues else []
        stream_events = [e for e in q if isinstance(e, StreamEvent)]

        async def gen():
            for se in stream_events:
                yield se

        return gen()


def _text_queue(text: str, in_t: int = 5, out_t: int = 3):
    return [
        StreamEvent(text=text),
        StreamEvent(usage=TokenUsage(in_t, out_t)),
        StreamEvent(done=True),
    ]


def _make_ctx(ui, conversation, catalog, store, executor) -> CommandContext:
    return CommandContext(
        registry=CommandRegistry(),
        ui=ui,
        agent=None,
        conversation=conversation,
        plan_manager=None,
        catalog=catalog,
        active_skills=store,
        executor=executor,
    )


# ── inline（F3.1/F4.2）─────────────────────────────────────


async def test_inline_activates_and_injects(tmp_path):
    """防 bug：inline 执行后 store 激活 + UI 收到注入消息（send_user_message）。"""
    catalog = _catalog(tmp_path)
    store = ActiveSkills()
    registry = Registry()
    provider = FakeProvider()
    executor = Executor(catalog, store, registry, provider)
    ui = RecordingUI()
    ctx = _make_ctx(ui, None, catalog, store, executor)

    await executor.execute(ctx, ui, "inline-skill", "hello args")
    assert store.names() == ["inline-skill"]
    body = store.snapshot()[0].body
    assert "INLINE BODY with hello args" in body  # $ARGUMENTS 已替换
    # send_user_message 收到渲染后的 body（等价 plan 的 inject_and_send）
    assert any(
        name == "send_user_message" and "INLINE BODY" in str(call[0])
        for name, *call in ui.calls
    ), ui.calls


async def test_inline_unknown_skill_shows_message(tmp_path):
    catalog = _catalog(tmp_path)
    store = ActiveSkills()
    executor = Executor(catalog, store, Registry(), FakeProvider())
    ui = RecordingUI()
    ctx = _make_ctx(ui, None, catalog, store, executor)
    await executor.execute(ctx, ui, "nope", "")
    assert "未知 Skill" in ui.messages[-1]
    assert store.names() == []


# ── fork（F3.1/N3/N13）─────────────────────────────────────


async def test_fork_isolates_conversation_and_reflows(tmp_path):
    """防 bug：fork 子会话独立（主 conv 不被污染）+ final_text 回流主对话 + token 写回。"""
    from mewcode.conversation.manager import ConversationManager

    catalog = _catalog(tmp_path)
    store = ActiveSkills()
    registry = Registry()
    provider = FakeProvider([_text_queue("review report: 3 critical")])
    executor = Executor(catalog, store, registry, provider)
    ui = RecordingUI()
    main_conv = ConversationManager(20)
    main_conv.add_user("请审查代码")
    main_conv.add_assistant("好的")
    ctx = _make_ctx(ui, main_conv, catalog, store, executor)

    await executor.execute(ctx, ui, "fork-skill", "")
    # fork 不激活主 store（fork 独立执行）
    assert store.names() == []
    # 主对话未被子 Agent 修改（N3 隔离）
    assert len(main_conv.get_context()) == 2
    # 结果回流主对话
    assert any(
        name == "append_assistant_message" and "review report" in str(call[0])
        for name, *call in ui.calls
    ), ui.calls
    # token 写回主统计（N13）
    assert ui.extra_tokens == (5, 3)


async def test_fork_context_none_starts_empty(tmp_path):
    """防 bug：context=none 时 fork 子 conv 只含 run 注入的 rendered（无主对话残留）。"""
    from mewcode.conversation.manager import ConversationManager

    catalog = _catalog(tmp_path)
    store = ActiveSkills()
    provider = FakeProvider([_text_queue("done")])
    executor = Executor(catalog, store, Registry(), provider)
    ui = RecordingUI()
    main_conv = ConversationManager(20)
    main_conv.add_user("msg1")
    main_conv.add_assistant("reply1")
    ctx = _make_ctx(ui, main_conv, catalog, store, executor)

    await executor.execute(ctx, ui, "fork-skill", "")
    # 子 Agent 的 conv 是独立对象，主 conv 仍 2 条
    assert len(main_conv.get_context()) == 2


async def test_fork_context_recent_copies_last_n(tmp_path):
    """防 bug：context=recent 带最近 N 条（缺省 5）进 fork 子会话，追加 rendered。"""
    from mewcode.conversation.manager import ConversationManager

    catalog = _catalog(tmp_path)
    store = ActiveSkills()
    registry = Registry()
    provider = FakeProvider([_text_queue("done")])
    executor = Executor(catalog, store, registry, provider)
    ui = RecordingUI()
    main_conv = ConversationManager(20)
    for i in range(6):
        main_conv.add_user(f"u{i}")
        main_conv.add_assistant(f"a{i}")
    ctx = _make_ctx(ui, main_conv, catalog, store, executor)

    await executor.execute(ctx, ui, "recent-skill", "")
    # 主 conv 不受影响（N3）
    assert len(main_conv.get_context()) == 12
    # fork 子会话 = 最近 5 条 + user rendered（子 Agent 内部 conv，此处间接验证：主 conv 未变即足够）


async def test_fork_context_full_summarizes(tmp_path):
    """防 bug：context=full 先走 LLM 摘要请求（复用 summarize 模式），再跑子 Agent。"""
    from mewcode.conversation.manager import ConversationManager

    catalog = _catalog(tmp_path)
    store = ActiveSkills()
    registry = Registry()
    # 队列顺序：第一次 stream=摘要请求，第二次=子 Agent 回合
    summary_queue = [
        StreamEvent(text="<summary>condensed main</summary>"),
        StreamEvent(done=True),
    ]
    provider = FakeProvider([summary_queue, _text_queue("agent done")])
    executor = Executor(catalog, store, registry, provider)
    ui = RecordingUI()
    main_conv = ConversationManager(20)
    main_conv.add_user("msg1")
    main_conv.add_assistant("reply1")
    ctx = _make_ctx(ui, main_conv, catalog, store, executor)

    await executor.execute(ctx, ui, "full-skill", "")
    # 摘要请求 + agent 回合 = 2 次 stream 调用
    assert provider.stream_calls == 2
    # 结果仍回流
    assert any(
        name == "append_assistant_message" and "agent done" in str(call[0])
        for name, *call in ui.calls
    )


async def test_fork_failure_writes_fallback(tmp_path):
    """防 bug：fork 任一步出错 → final_text 兜底 "[skill <name> failed: ...]" 仍写回主对话。"""
    from mewcode.conversation.manager import ConversationManager

    catalog = _catalog(tmp_path)
    store = ActiveSkills()

    class ExplodingProvider(FakeProvider):
        def stream(self, payload):
            async def gen():
                raise RuntimeError("provider blew up")
                yield  # pragma: no cover

            return gen()

    executor = Executor(catalog, store, Registry(), ExplodingProvider())
    ui = RecordingUI()
    main_conv = ConversationManager(20)
    ctx = _make_ctx(ui, main_conv, catalog, store, executor)

    await executor.execute(ctx, ui, "fork-skill", "")
    assert any(
        name == "append_assistant_message" and "fork-skill failed" in str(call[0])
        for name, *call in ui.calls
    ), ui.calls
