"""Smoke tests for the JSONL store."""
import json
import pytest
from pathlib import Path

from chatroom.store import Store


@pytest.fixture
def tmp_store(tmp_path: Path) -> Store:
    return Store(tmp_path)


def _msg(i, **over):
    base = {"from": "human", "type": "finding",
            "timestamp": f"2026-09-14T15:00:0{i}+08:00", "subject": f"msg {i}"}
    base.update(over)
    return base


def test_store_empty_init(tmp_path):
    s = Store(tmp_path)
    assert s.read_all() == []
    assert s.messages_path.exists()


def test_append_assigns_id_and_timestamp(tmp_store):
    m = _msg(1)
    out = tmp_store.append(m)
    assert out["id"].startswith("msg-")
    assert "timestamp" in out
    assert tmp_store.read_all()[0]["id"] == out["id"]


def test_append_rejects_invalid(tmp_store):
    with pytest.raises(ValueError, match="invalid"):
        tmp_store.append({"from": "x", "type": "spam", "subject": "y"})


def test_ids_monotonic(tmp_store):
    for i in range(5):
        tmp_store.append(_msg(i))
    ids = [m["id"] for m in tmp_store.read_all()]
    nums = [int(i.split("-")[1]) for i in ids]
    assert nums == sorted(nums)
    assert len(set(ids)) == 5


def test_handles_existing_jsonl(tmp_path):
    p = tmp_path / "messages.jsonl"
    p.write_text(
        json.dumps({"id": "msg-0010", "from": "h", "type": "finding",
                    "timestamp": "t", "subject": "a"}, ensure_ascii=False) + chr(10),
        encoding="utf-8",
    )
    s = Store(tmp_path)
    out = s.append(_msg(1))
    assert out["id"] == "msg-0011"


def test_read_returns_jsonl_messages(tmp_store):
    for i in range(3):
        tmp_store.append(_msg(i))
    msgs = tmp_store.read_all()
    assert len(msgs) == 3
