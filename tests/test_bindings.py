"""Tests for the session binding protocol (user-decided host session)."""

import pytest
from fastapi.testclient import TestClient

from chatroom.server import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    return TestClient(app), app


def _report_host(c, name, host_sessions, bound=None):
    return c.post(
        f"/api/agent/{name}/host",
        json={
            "host_sessions": host_sessions,
            "bound_session_id": bound,
        },
    )


def test_report_host_auto_registers(client):
    c, app = client
    r = _report_host(c, "opencode", [{"id": "s1", "title": "t"}])
    assert r.status_code == 200
    assert r.json()["name"] == "opencode"
    assert app.state.sessions.get("opencode") is not None


def test_report_host_sets_host_sessions_and_bound(client):
    c, app = client
    _report_host(
        c, "opencode", [{"id": "s1", "title": "a"}, {"id": "s2", "title": "b"}], bound="s2"
    )
    sess = app.state.sessions.get("opencode")
    assert [s["id"] for s in sess.host_sessions] == ["s1", "s2"]
    assert sess.bound_session_id == "s2"


def test_bind_queues_auto_creating_missing_session(client):
    c, app = client
    r = c.post("/api/agent/ghost/bind", json={"op": "bind", "session_id": "s9"})
    assert r.status_code == 200
    sess = app.state.sessions.get("ghost")
    assert sess is not None
    assert sess.pending_command["op"] == "bind"
    assert sess.pending_command["session_id"] == "s9"
    assert "ts" in sess.pending_command


def test_commands_returns_pending(client):
    c, _app = client
    c.post("/api/agent/opencode/bind", json={"op": "create"})
    r = c.get("/api/agent/opencode/commands")
    cmds = r.json()["commands"]
    assert len(cmds) == 1
    assert cmds[0]["op"] == "create"


def test_ack_clears_pending_and_updates_binding(client):
    c, app = client
    _report_host(c, "opencode", [{"id": "s1"}, {"id": "s2"}])
    c.post("/api/agent/opencode/bind", json={"op": "bind", "session_id": "s2"})
    r = c.post("/api/agent/opencode/commands/ack", json={"bound_session_id": "s2"})
    assert r.status_code == 200
    sess = app.state.sessions.get("opencode")
    assert sess.pending_command is None
    assert sess.bound_session_id == "s2"


def test_bind_invalid_op_400(client):
    c, _app = client
    r = c.post("/api/agent/opencode/bind", json={"op": "explode"})
    assert r.status_code == 400


def test_unbind_clears_binding(client):
    c, app = client
    _report_host(c, "opencode", [{"id": "s1"}], bound="s1")
    c.post("/api/agent/opencode/bind", json={"op": "unbind"})
    c.post("/api/agent/opencode/commands/ack", json={"bound_session_id": None})
    sess = app.state.sessions.get("opencode")
    assert sess.bound_session_id is None
    assert sess.pending_command is None
