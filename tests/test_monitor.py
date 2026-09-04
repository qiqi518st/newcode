import json

from newcode.monitor.app import _workspace_groups, _workspace_records
from newcode.monitor.protocol import (
    MonitorLease,
    is_monitor_active,
    write_request_record,
)
from newcode.prompt.assembler import PromptPayload
from newcode.provider.base import Message


def test_monitor_lease_controls_tracing(tmp_path):
    lease = MonitorLease(str(tmp_path))
    assert not is_monitor_active(str(tmp_path))

    lease.start()
    assert is_monitor_active(str(tmp_path))

    lease.close()
    assert not is_monitor_active(str(tmp_path))


def test_request_record_contains_assembled_and_provider_history(tmp_path):
    path = tmp_path / "request.json"
    payload = PromptPayload(
        stable_prompt="stable",
        env_segment="env",
        messages=[Message(role="user", content="hello")],
        trace_context={
            "path": str(path),
            "session_id": "session-1",
            "pid": 123,
            "workspace": str(tmp_path),
            "request_kind": "conversation",
            "user_input": "hello",
            "turn": 0,
        },
    )
    write_request_record(
        payload,
        "openai",
        "test-model",
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["user_input"] == "hello"
    assert record["assembled_history"][0]["role"] == "user"
    assert record["provider_request"]["messages"][0]["content"] == "hello"


def test_monitor_records_are_sorted_newest_first(tmp_path):
    request_dir = tmp_path / ".newcode" / "sessions" / "s1" / "requests"
    request_dir.mkdir(parents=True)
    for name, timestamp in (("request-000001.json", 1), ("request-000002.json", 2)):
        (request_dir / name).write_text(
            json.dumps({"recorded_at": timestamp, "session_id": "s1"}),
            encoding="utf-8",
        )

    records = _workspace_records(str(tmp_path))
    assert [record["recorded_at"] for record in records] == [2, 1]


def test_monitor_groups_calls_by_user_submission(tmp_path):
    request_dir = tmp_path / ".newcode" / "sessions" / "s1" / "requests"
    request_dir.mkdir(parents=True)
    records = [
        {"recorded_at": 1, "session_id": "s1", "run_id": "run-a", "user_input": "a"},
        {"recorded_at": 2, "session_id": "s1", "run_id": "run-a", "user_input": "a"},
        {"recorded_at": 3, "session_id": "s1", "run_id": "run-b", "user_input": "b"},
    ]
    for index, record in enumerate(records, start=1):
        (request_dir / f"request-{index:06d}.json").write_text(
            json.dumps(record), encoding="utf-8"
        )

    groups = _workspace_groups(str(tmp_path))
    assert [len(group) for group in groups] == [1, 2]
    assert groups[0][0]["user_input"] == "b"
