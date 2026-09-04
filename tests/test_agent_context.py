"""Agent/context integration coverage for ch08."""

import os
from typing import ClassVar

import pytest

from newcode.agent import Agent, EventType, StopReason
from newcode.context.summarize import CompactOutcome
from newcode.conversation.manager import ConversationManager
from newcode.llm import PromptTooLongError
from newcode.provider.base import StreamEvent, TokenUsage, ToolCall, ToolResult
from newcode.tools.registry import Registry


class FakeProvider:
    def __init__(self, streams):
        self.streams = list(streams)
        self.calls = 0

    @property
    def name(self):
        return "fake"

    @property
    def model(self):
        return "fake-model"

    async def stream(self, payload):
        index = self.calls
        self.calls += 1
        for event in self.streams[min(index, len(self.streams) - 1)]:
            yield event


class FakeContextManager:
    def __init__(self):
        self.manage_calls = []
        self.force_calls = 0
        self.anchor_calls = []
        self.event_target = None

    async def manage_context(self, tool_defs):
        self.manage_calls.append(tool_defs)
        if self.event_target is not None:
            self.event_target._context_events.append(("context_compacting", "auto"))
            self.event_target._context_events.append(
                ("context_offloaded", {"count": 2, "spill_dir": "spill"})
            )

    async def force_compact(self, tool_defs):
        self.force_calls += 1
        if self.event_target is not None:
            self.event_target._context_events.extend(
                [
                    ("context_compacting", "force"),
                    ("context_offloaded", {"count": 1, "spill_dir": "spill"}),
                    (
                        "context_compacted",
                        CompactOutcome(True, 100, 20, 1, True, "", []),
                    ),
                ]
            )
        return CompactOutcome(True, 100, 20, 0, True, "", [])

    def update_anchor(self, usage, conv_len):
        self.anchor_calls.append((usage, conv_len))


class FakeFileTracker:
    def __init__(self, conv):
        self.conv = conv
        self.calls = []
        self.saw_tool_result = None

    async def record(self, path, content):
        self.calls.append((path, content))
        self.saw_tool_result = any(
            message.role == "tool" for message in self.conv.get_context()
        )


class ReadTool:
    name = "read_file"
    description = "read a file"
    parameters: ClassVar[dict] = {"type": "object"}
    read_only = True

    async def execute(self, arguments):
        return ToolResult(status="ok", output="file contents")


class LoopTool:
    name = "loop_tool"
    description = "loop"
    parameters: ClassVar[dict] = {"type": "object"}
    read_only = True

    async def execute(self, arguments):
        return ToolResult(status="ok", output="done")


def make_agent(provider, context_mgr=None, file_tracker=None, registry=None):
    conv = ConversationManager(20)
    agent = Agent(
        provider,
        conv,
        registry or Registry(),
        "stable",
        "env",
        context_mgr=context_mgr,
        file_tracker=file_tracker,
    )
    if context_mgr is not None:
        context_mgr.event_target = agent
    return agent, conv


async def collect(agent, user_input="hello"):
    return [event async for event in agent.run(user_input)]


@pytest.mark.anyio
async def test_manage_context_called_each_turn():
    provider = FakeProvider(
        [
            [
                StreamEvent(tool_call=ToolCall("loop_tool", {})),
                StreamEvent(done=True, usage=TokenUsage(10, 2)),
            ],
            [
                StreamEvent(text="finished"),
                StreamEvent(done=True, usage=TokenUsage(12, 3)),
            ],
        ]
    )
    registry = Registry()
    registry.register(LoopTool())
    context_mgr = FakeContextManager()
    agent, _ = make_agent(provider, context_mgr=context_mgr, registry=registry)

    events = await collect(agent, "use the tool")

    assert len(context_mgr.manage_calls) == 2
    assert all(
        calls is context_mgr.manage_calls[0] for calls in context_mgr.manage_calls
    )
    assert events[-1].payload == StopReason.NATURAL


@pytest.mark.anyio
async def test_backward_compat_without_context_mgr():
    provider = FakeProvider([[StreamEvent(text="ok"), StreamEvent(done=True)]])
    agent, _ = make_agent(provider)

    events = await collect(agent)

    assert [event.type for event in events][-1] == EventType.DONE
    assert events[-1].payload == StopReason.NATURAL


@pytest.mark.anyio
async def test_ptl_triggers_force_compact_retry_once():
    provider = FakeProvider(
        [
            [StreamEvent(err=PromptTooLongError("too long"))],
            [
                StreamEvent(text="recovered"),
                StreamEvent(done=True, usage=TokenUsage(8, 2)),
            ],
        ]
    )
    context_mgr = FakeContextManager()
    agent, _ = make_agent(provider, context_mgr=context_mgr)

    events = await collect(agent)

    assert provider.calls == 2
    assert context_mgr.force_calls == 1
    assert "recovered" in [
        event.payload for event in events if event.type == EventType.TEXT
    ]
    assert events[-1].payload == StopReason.NATURAL
    assert [
        event.payload for event in events if event.type == EventType.CONTEXT_COMPACTING
    ] == ["auto", "force"]
    assert any(event.type == EventType.CONTEXT_OFFLOADED for event in events)
    assert any(event.type == EventType.CONTEXT_COMPACTED for event in events)


@pytest.mark.anyio
async def test_second_ptl_no_second_compact():
    provider = FakeProvider(
        [
            [StreamEvent(err=PromptTooLongError("first"))],
            [StreamEvent(err=PromptTooLongError("second"))],
        ]
    )
    context_mgr = FakeContextManager()
    agent, _ = make_agent(provider, context_mgr=context_mgr)

    events = await collect(agent)

    assert provider.calls == 2
    assert context_mgr.force_calls == 1
    assert events[-1].payload == StopReason.STREAM_ERROR


@pytest.mark.anyio
async def test_read_file_tracks_recovery(tmp_path):
    provider = FakeProvider(
        [
            [
                StreamEvent(
                    tool_call=ToolCall("read_file", {"path": str(tmp_path / "x.py")})
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    registry = Registry()
    registry.register(ReadTool())
    conv = ConversationManager(20)
    tracker = FakeFileTracker(conv)
    agent = Agent(provider, conv, registry, file_tracker=tracker)

    await collect(agent)

    assert tracker.calls == [(os.path.abspath(str(tmp_path / "x.py")), "file contents")]
    assert tracker.saw_tool_result is False


@pytest.mark.anyio
async def test_update_anchor_after_main_stream():
    usage = TokenUsage(33, 7)
    provider = FakeProvider(
        [[StreamEvent(text="ok"), StreamEvent(done=True, usage=usage)]]
    )
    context_mgr = FakeContextManager()
    agent, conv = make_agent(provider, context_mgr=context_mgr)

    await collect(agent)

    assert context_mgr.anchor_calls == [(usage, len(conv.get_messages_ref()) - 1)]


@pytest.mark.anyio
async def test_emit_compact_events():
    provider = FakeProvider([[StreamEvent(text="ok"), StreamEvent(done=True)]])
    context_mgr = FakeContextManager()
    agent, _ = make_agent(provider, context_mgr=context_mgr)

    events = await collect(agent)
    compact_events = [
        event for event in events if event.type == EventType.CONTEXT_COMPACTING
    ]

    assert [event.payload for event in compact_events] == ["auto"]


@pytest.mark.anyio
async def test_context_offload_event_is_forwarded():
    provider = FakeProvider([[StreamEvent(text="ok"), StreamEvent(done=True)]])
    context_mgr = FakeContextManager()
    agent, _ = make_agent(provider, context_mgr=context_mgr)

    events = await collect(agent)

    event = next(e for e in events if e.type == EventType.CONTEXT_OFFLOADED)
    assert event.payload == {"count": 2, "spill_dir": "spill"}
