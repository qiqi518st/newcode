"""SessionRecovery 单测（ch09 T8，spec F9 / AC12-AC15）。

防 bug：中间坏行吞掉后续消息、compact 起点错误、多 tool call 未全部闭合仍被保留、
孤立 tool result 被伪造配对、6 小时提醒阈值错误。
"""

import json
import time
from pathlib import Path

import pytest

from mewcode.context.session import new_session_context
from mewcode.session.recovery import recover_session, recover_session_async


@pytest.fixture
def session(tmp_path):
    return new_session_context(str(tmp_path))


def _write(session, lines: list[dict | str]) -> Path:
    path = Path(session.conversation_path)
    with path.open("w", encoding="utf-8") as fh:
        for line in lines:
            if isinstance(line, str):
                fh.write(line + "\n")
            else:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    return path


def _msg(role, content="", ts=100, **kw):
    d = {"role": role, "content": content, "ts": ts}
    d.update(kw)
    return d


def test_recover_from_start_no_compact(session):
    """防 bug：无 compact 标记时从第一条有效消息开始恢复。"""
    _write(
        session,
        [
            _msg("user", "u1"),
            _msg("assistant", "a1"),
            _msg("user", "u2"),
        ],
    )
    result = recover_session(session.session_dir, now=1000)
    assert [m.content for m in result.messages] == ["u1", "a1", "u2"]
    assert result.truncated is False


def test_recover_from_last_compact(session):
    """防 bug：有 compact 标记时只恢复最后标记之后的消息（AC14）。"""
    _write(
        session,
        [
            _msg("user", "before1"),
            _msg("user", "before2"),
            {"type": "compact", "ts": 200},
            _msg("user", "after1"),
            _msg("assistant", "after2"),
        ],
    )
    result = recover_session(session.session_dir, now=1000)
    assert [m.content for m in result.messages] == ["after1", "after2"]


def test_bad_line_in_middle_skipped(session):
    """防 bug：中间非法 JSON 行被跳过，不影响前后消息（AC12）。"""
    _write(
        session,
        [
            _msg("user", "u1"),
            "not-json{{{",
            _msg("assistant", "a1"),
        ],
    )
    result = recover_session(session.session_dir, now=1000)
    assert [m.content for m in result.messages] == ["u1", "a1"]
    assert result.skipped_lines == [2]


def test_trailing_bad_line_skipped(session):
    """防 bug：尾部半行（崩溃残留）被跳过，前面完整行可恢复。"""
    _write(
        session,
        [
            _msg("user", "u1"),
            _msg("assistant", "a1"),
        ],
    )
    with open(Path(session.conversation_path), "a", encoding="utf-8") as fh:
        fh.write('{"role":"user","content":"trunc')
    result = recover_session(session.session_dir, now=1000)
    assert [m.content for m in result.messages] == ["u1", "a1"]


def test_tool_pairing_complete(session):
    """防 bug：完整闭合的工具对保留，配对顺序不破坏。"""
    _write(
        session,
        [
            _msg("user", "u1"),
            _msg("assistant", "", tool_calls=[{"id": "c1", "name": "t", "arguments": {}}]),
            _msg("tool", "result", tool_call_id="c1", tool_use_id="c1", name="t"),
            _msg("assistant", "done"),
        ],
    )
    result = recover_session(session.session_dir, now=1000)
    assert [m.content for m in result.messages] == ["u1", "", "result", "done"]


def test_unpaired_tool_call_truncated(session):
    """防 bug：末尾 assistant 未闭合的 tool call 截断到该消息之前（AC13）。"""
    _write(
        session,
        [
            _msg("user", "u1"),
            _msg("assistant", "will be cut", tool_calls=[{"id": "c1", "name": "t", "arguments": {}}]),
        ],
    )
    result = recover_session(session.session_dir, now=1000)
    assert [m.content for m in result.messages] == ["u1"]
    assert result.truncated is True


def test_partial_multi_tool_call_truncated(session):
    """防 bug：多个 tool call 只闭合部分时，整个 assistant 仍视为未闭合。"""
    _write(
        session,
        [
            _msg("user", "u1"),
            _msg(
                "assistant",
                "",
                tool_calls=[
                    {"id": "c1", "name": "t", "arguments": {}},
                    {"id": "c2", "name": "t", "arguments": {}},
                ],
            ),
            _msg("tool", "r1", tool_call_id="c1", tool_use_id="c1", name="t"),
            # c2 缺结果
        ],
    )
    result = recover_session(session.session_dir, now=1000)
    assert [m.content for m in result.messages] == ["u1"]
    assert result.truncated is True


def test_orphan_tool_result_dropped(session):
    """防 bug：孤立 tool result 不伪造配对，直接跳过（AC13）。"""
    _write(
        session,
        [
            _msg("user", "u1"),
            _msg("assistant", "a1"),
            _msg("tool", "orphan", tool_call_id="nope", tool_use_id="nope", name="t"),
            _msg("user", "u2"),
        ],
    )
    result = recover_session(session.session_dir, now=1000)
    # 孤立 tool result 被跳过，但后续 user 消息不受影响（不误截断）
    assert [m.content for m in result.messages] == ["u1", "a1", "u2"]
    assert any("orphan" in d for d in result.diagnostics)


def test_interleaved_tool_pair_completed(session):
    """防 bug：跨多个 user 的 tool 对（同一 assistant 的多个调用夹着新 user）不得破坏。"""
    _write(
        session,
        [
            _msg("user", "u1"),
            _msg(
                "assistant",
                "",
                tool_calls=[
                    {"id": "c1", "name": "t", "arguments": {}},
                    {"id": "c2", "name": "t", "arguments": {}},
                ],
            ),
            _msg("tool", "r1", tool_call_id="c1", tool_use_id="c1", name="t"),
            _msg("tool", "r2", tool_call_id="c2", tool_use_id="c2", name="t"),
            _msg("assistant", "final"),
        ],
    )
    result = recover_session(session.session_dir, now=1000)
    assert len(result.messages) == 5
    assert result.messages[-1].content == "final"


def test_time_reminder_over_6h(session):
    """防 bug：暂停超过 6 小时追加固定提醒（UTC 时间计算）。"""
    now = time.time()
    _write(session, [_msg("user", "old", ts=now - 10 * 3600)])
    result = recover_session(session.session_dir, now=now)
    assert result.time_reminder is not None
    assert "本会话已暂停" in result.time_reminder
    assert result.messages[-1].role == "user"
    assert "已暂停" in (result.messages[-1].content or "")


def test_no_time_reminder_under_6h(session):
    """防 bug：暂停不足 6 小时不加提醒。"""
    now = time.time()
    _write(session, [_msg("user", "recent", ts=now - 3600)])
    result = recover_session(session.session_dir, now=now)
    assert result.time_reminder is None
    assert len(result.messages) == 1


def test_missing_file_no_crash(session):
    """防 bug：JSONL 文件不存在时返回空结果而非崩溃。"""
    Path(session.conversation_path).unlink()
    result = recover_session(session.session_dir, now=1000)
    assert result.messages == []
    assert result.diagnostics


# ---------- AC15：token 超限 → 一次压缩 → 整组降级 ----------


def _long_messages(n, fill="A" * 2000):
    """构造 n 条 user/assistant 对，每条约 2000 字符。"""
    out = []
    for i in range(n):
        out.append(_msg("user", f"u{i}" + fill, ts=100 + i))
        out.append(_msg("assistant", f"a{i}" + fill, ts=101 + i))
    return out


def test_recover_under_limit_no_drop(session):
    """防 bug：不超限时不得丢弃或触发压缩。"""
    _write(session, _long_messages(3))
    result = recover_session(session.session_dir, now=1000, context_window=100_000)
    assert len(result.messages) == 6
    assert result.truncated is False


def test_recover_over_limit_sync_drops_groups(session):
    """防 bug：同步路径超限时按完整 user 组整组丢弃（不拆对）。"""
    # 每条 ~2000 字符 ≈ 570 token；6 条 ≈ 3400 token，窗口 1000 → 必须降级
    _write(session, _long_messages(6))
    result = recover_session(session.session_dir, now=1000, context_window=1000)
    assert result.truncated is True
    # 整组丢弃：剩下的消息仍是完整 user/assistant 对
    assert len(result.messages) % 2 == 0
    assert result.messages[0].role == "user"


@pytest.mark.anyio
async def test_recover_async_compressor_once(session):
    """防 bug：超限时恰好调用一次压缩窄接口（AC15）。"""
    calls = []

    async def compressor(messages):
        calls.append(len(messages))
        return messages[:2]  # 压缩为前 2 条

    _write(session, _long_messages(6))
    result = await recover_session_async(
        session.session_dir,
        now=1000,
        context_window=1000,
        compressor=compressor,
    )
    assert len(calls) == 1
    assert result.compacted is True
    assert len(result.messages) == 2


@pytest.mark.anyio
async def test_recover_async_compressor_failure_falls_back(session):
    """防 bug：压缩失败时记录诊断并整组降级，不崩溃（AC15/N11）。"""
    async def compressor(messages):
        raise RuntimeError("network down")

    _write(session, _long_messages(6))
    result = await recover_session_async(
        session.session_dir,
        now=1000,
        context_window=1000,
        compressor=compressor,
    )
    assert result.compacted is False
    assert any("compaction failed" in d for d in result.diagnostics)
    assert result.truncated is True
    assert len(result.messages) % 2 == 0


@pytest.mark.anyio
async def test_recover_async_compressed_still_over_drops(session):
    """防 bug：压缩后仍超限时继续整组降级。"""
    async def compressor(messages):
        # 假装压缩后只剩 4 条，但仍超窗口
        return messages[:4]

    _write(session, _long_messages(6))
    result = await recover_session_async(
        session.session_dir,
        now=1000,
        context_window=1000,
        compressor=compressor,
    )
    assert result.compacted is True
    assert result.truncated is True
    assert len(result.messages) < 4


@pytest.mark.anyio
async def test_recover_async_under_limit_no_compressor_call(session):
    """防 bug：不超限时压缩器不被调用。"""
    calls = []

    async def compressor(messages):
        calls.append(messages)
        return messages

    _write(session, _long_messages(2))
    result = await recover_session_async(
        session.session_dir,
        now=1000,
        context_window=100_000,
        compressor=compressor,
    )
    assert calls == []
    assert result.compacted is False
    assert len(result.messages) == 4
