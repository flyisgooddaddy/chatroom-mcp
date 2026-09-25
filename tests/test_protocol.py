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


def test_busy_minimal_well_formed():
    """busy is a NOTIFY type (per COLLABORATION §10 接入器中断协议).

    No brief required, no in_reply_to required.
    Used by B's bridge to tell A "B 忙" so A can defer.
    """
    msg = {
        "id": "msg-busy-0001",
        "from": "openwriter",
        "type": "busy",
        "timestamp": "2026-09-25T12:35:00+08:00",
        "subject": "openwriter 正在做 interrupt commit, 稍后接 @",
        "to": "opencode",
        "in_reply_to": "msg-9000",
    }
    ok, reason = is_well_formed(msg)
    assert ok, reason


def test_busy_bare_well_formed():
    """busy with only required fields (no brief, no in_reply_to) — still OK."""
    msg = {
        "id": "msg-busy-0002",
        "from": "openwriter",
        "type": "busy",
        "timestamp": "2026-09-25T12:35:00+08:00",
        "subject": "B 忙",
    }
    ok, reason = is_well_formed(msg)
    assert ok, reason


def test_busy_in_valid_types():
    """busy must be in VALID_TYPES."""
    from chatroom.protocol import TYPES_NOTIFY, VALID_TYPES

    assert "busy" in VALID_TYPES
    assert "busy" in TYPES_NOTIFY


def test_busy_not_in_brief_or_reply_required():
    """busy is NOT required to carry brief or in_reply_to (it's a NOTIFY)."""
    from chatroom.protocol import TYPES_NEED_BRIEF, TYPES_NEED_REPLY

    assert "busy" not in TYPES_NEED_BRIEF
    assert "busy" not in TYPES_NEED_REPLY


def test_old_messages_still_well_formed():
    """Backwards compat: existing types still parse; plan/request/done need brief,
    ack/requestion/done need in_reply_to (they always did — busy is purely
    additive)."""
    for typ in ["finding", "plan", "request", "ack", "requestion", "done", "blocked"]:
        msg = {
            "id": f"msg-{typ}-0001",
            "from": "workbuddy",
            "type": typ,
            "timestamp": "2026-09-25T12:35:00+08:00",
            "subject": f"{typ} legacy",
        }
        if typ in ("plan", "request", "done"):
            msg["brief"] = {"what": "", "why": "", "how": "", "criteria": "", "not_doing": ""}
        if typ in ("ack", "requestion", "done"):
            msg["in_reply_to"] = f"msg-{typ}-0000"
        ok, reason = is_well_formed(msg)
        assert ok, f"{typ} should still parse: {reason}"
