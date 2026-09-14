"""Smoke tests for the protocol validator (P1)."""
from chatroom.protocol import is_well_formed


def test_minimal_well_formed():
    msg = {
        "id": "msg-0001",
        "from": "human",
        "type": "finding",
        "timestamp": "2026-09-14T15:00:00+08:00",
        "subject": "hi",
    }
    ok, reason = is_well_formed(msg)
    assert ok, reason


def test_plan_requires_brief():
    msg = {
        "id": "msg-0002",
        "from": "workbuddy",
        "type": "plan",
        "timestamp": "2026-09-14T15:00:00+08:00",
        "subject": "do something",
    }
    ok, reason = is_well_formed(msg)
    assert not ok
    assert "brief" in reason


def test_ack_requires_in_reply_to():
    msg = {
        "id": "msg-0003",
        "from": "opencode",
        "type": "ack",
        "timestamp": "2026-09-14T15:00:00+08:00",
        "subject": "OK",
    }
    ok, reason = is_well_formed(msg)
    assert not ok
    assert "in_reply_to" in reason


def test_invalid_type():
    msg = {
        "id": "msg-0004",
        "from": "human",
        "type": "spam",
        "timestamp": "2026-09-14T15:00:00+08:00",
        "subject": "x",
    }
    ok, reason = is_well_formed(msg)
    assert not ok
    assert "invalid type" in reason


def test_missing_field():
    msg = {"id": "msg-0005", "from": "human", "type": "finding", "subject": "x"}
    ok, reason = is_well_formed(msg)
    assert not ok
    assert "timestamp" in reason
