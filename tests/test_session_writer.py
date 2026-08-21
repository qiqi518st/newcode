"""SessionWriter 单测（ch09 T4/T5，spec F6-F7 / AC5-AC8）。

防 bug：JSONL 追加不重写旧行、model 只写首条 user、compact 标记先于压缩消息、
并发追加交错行、close 后继续写不抛半写、Entry/Message 往返丢字段。
"""

import json
from pathlib import Path

import pytest

from mewcode.context.session import new_session_context
from mewcode.provider.base import Message
from mewcode.session.writer import (
    SessionWriter,
    entry_from_message,
    message_from_entry,
)


@pytest.fixture
def session(tmp_path):
    return new_session_context(str(tmp_path))


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_entry_from_message_roundtrip():
    """防 bug：Message 序列化为 Entry 再反序列化必须保字段（含 tool id）。"""
    msg = Message(
        role="assistant",
        content="",
        tool_calls=[
            {"id": "call_1", "name": "read_file", "arguments": {"path": "a.py"}}
        ],
    )
    entry = entry_from_message(msg)
    assert entry.role == "assistant"
    assert entry.tool_calls[0]["id"] == "call_1"
    back = message_from_entry(entry)
    assert back.role == "assistant"
    assert back.tool_calls[0]["id"] == "call_1"


def test_message_from_entry_invalid_role():
    """防 bug：非法 role 不能伪造出 Message。"""
    assert message_from_entry({"role": "nope", "content": "x"}) is None
    assert message_from_entry({"content": "no role"}) is None


def test_jsonl_structure(session):
    """防 bug：user/assistant/tool 各写合法 JSON 行，首条 user 携带 model。"""
    writer = SessionWriter(session.session_dir, model="mock-model")
    writer.append_message(Message(role="user", content="hi"))
    writer.append_message(
        Message(
            role="assistant",
            content="",
            tool_calls=[{"id": "c1", "name": "t", "arguments": {}}],
        )
    )
    writer.append_message(
        Message(
            role="tool",
            content="result",
            tool_call_id="c1",
            tool_use_id="c1",
            name="t",
        )
    )
    writer.close()
    lines = _read_lines(Path(session.conversation_path))
    assert lines[0]["role"] == "user"
    assert lines[0]["model"] == "mock-model"
    assert lines[0]["ts"] > 0
    assert lines[1]["tool_calls"][0]["id"] == "c1"
    assert lines[2]["tool_call_id"] == "c1"
    assert lines[2]["name"] == "t"


def test_model_only_on_first_user(session):
    """防 bug：model 只能写在第一条 user 消息上，后续 user 不再重复。"""
    writer = SessionWriter(session.session_dir, model="m")
    writer.append_message(Message(role="user", content="first"))
    writer.append_message(Message(role="assistant", content="reply"))
    writer.append_message(Message(role="user", content="second"))
    writer.close()
    lines = _read_lines(Path(session.conversation_path))
    assert lines[0].get("model") == "m"
    assert "model" not in lines[2]


def test_append_does_not_rewrite(session):
    """防 bug：追加后既有行不能被改写（崩溃安全前提）。"""
    writer = SessionWriter(session.session_dir)
    writer.append_message(Message(role="user", content="one"))
    path = Path(session.conversation_path)
    first = path.read_text(encoding="utf-8")
    writer.append_message(Message(role="user", content="two"))
    writer.close()
    assert path.read_text(encoding="utf-8").startswith(first)


def test_append_all_and_compact_order(session):
    """防 bug：compact 标记必须先于压缩后的消息写入（AC8）。"""
    writer = SessionWriter(session.session_dir)
    writer.append_message(Message(role="user", content="u1"))
    writer.write_compact_marker()
    writer.append_all(
        [Message(role="user", content="s1"), Message(role="user", content="s2")]
    )
    writer.close()
    lines = _read_lines(Path(session.conversation_path))
    assert lines[1]["type"] == "compact"
    assert lines[2]["content"] == "s1"
    assert lines[3]["content"] == "s2"


def test_close_idempotent(session):
    """防 bug：close 重复调用不抛异常（退出路径可能多次 close）。"""
    writer = SessionWriter(session.session_dir)
    writer.append_message(Message(role="user", content="x"))
    writer.close()
    writer.close()


def test_write_after_close_raises(session):
    """防 bug：closed Writer 继续写必须显式报错而非静默丢数据。"""
    writer = SessionWriter(session.session_dir)
    writer.close()
    with pytest.raises(RuntimeError):
        writer.append_message(Message(role="user", content="late"))


def test_context_manager(session):
    """防 bug：with 语句退出自动 close。"""
    with SessionWriter(session.session_dir) as writer:
        writer.append_message(Message(role="user", content="in-with"))
    # 关闭后可正常读取
    assert Path(session.conversation_path).read_text(encoding="utf-8").strip()


def test_concurrent_appends_no_interleave(tmp_path):
    """防 bug：多线程并发追加不能产生交错半行（锁保证单行原子）。"""
    session = new_session_context(str(tmp_path))
    writer = SessionWriter(session.session_dir)
    import threading

    errors = []

    def worker(n):
        try:
            for i in range(50):
                writer.append_message(Message(role="user", content=f"w{n}-{i}"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    writer.close()
    assert not errors
    lines = _read_lines(Path(session.conversation_path))
    assert len(lines) == 200
    for line in lines:
        assert isinstance(line["content"], str) and line["content"]
        assert json.dumps(line, ensure_ascii=False)  # 每行都是合法 JSON


def test_tool_results_grouped_compat(session):
    """防 bug：grouped tool_results 事件可写可跳过，不破坏逐条 tool id 语义。"""
    writer = SessionWriter(session.session_dir)
    writer.append_event(
        "tool_results",
        tool_results=[{"id": "c1", "output": "o"}],
    )
    writer.close()
    lines = _read_lines(Path(session.conversation_path))
    assert lines[0]["type"] == "tool_results"
    assert lines[0]["tool_results"][0]["id"] == "c1"
