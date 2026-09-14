"""HTTP-level tests for the chatroom-mcp server + Web GUI."""
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from chatroom.server import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    return TestClient(app), tmp_path


def test_root_returns_index_or_500(tmp_path):
    app = create_app(tmp_path)
    c = TestClient(app)
    r = c.get("/")
    assert r.status_code in (200, 500)


def test_api_messages_empty(client):
    c, _ = client
    r = c.get("/api/messages")
    assert r.status_code == 200
    assert r.json() == {"messages": []}


def test_api_sessions_empty(client):
    c, _ = client
    r = c.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == {"sessions": []}


def test_api_post_then_messages(client):
    c, _ = client
    payload = {"msg": {"from": "human", "type": "finding",
                       "timestamp": "2026-09-14T15:00:00+08:00",
                       "subject": "hello gui"}}
    r = c.post("/api/post", json=payload)
    assert r.status_code == 200
    posted = r.json()
    assert posted["subject"] == "hello gui"
    assert posted["id"].startswith("msg-")

    r = c.get("/api/messages")
    msgs = r.json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["subject"] == "hello gui"


def test_api_post_rejects_bad_payload(client):
    c, _ = client
    r = c.post("/api/post", json={"msg": "not a dict"})
    assert r.status_code in (400, 422)


def test_api_post_rejects_invalid_message(client):
    c, _ = client
    r = c.post("/api/post", json={"msg": {"from": "h", "type": "spam"}})
    assert r.status_code in (400, 422)


def test_kick_session(client):
    c, _tmp = client
    # Use the same registry the app holds (exposed via app.state).
    reg = c.app.state.sessions
    reg.handshake("workbuddy", callback_url="http://x")
    r = c.delete("/api/sessions/workbuddy")
    assert r.status_code == 200
    assert r.json() == {"removed": "workbuddy"}
    assert reg.get("workbuddy") is None


def test_kick_missing_returns_404(client):
    c, _ = client
    r = c.delete("/api/sessions/never-existed")
    assert r.status_code == 404


def test_static_dir_mounted_if_present():
    from chatroom.server import WEB_DIR
    static = WEB_DIR / "static"
    static.mkdir(parents=True, exist_ok=True)
    target = static / "test_tmp.txt"
    target.write_text("hi", encoding="utf-8")
    try:
        from chatroom.server import create_app
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            app = create_app(Path(td))
            c = TestClient(app)
            r = c.get("/static/test_tmp.txt")
            assert r.status_code == 200
            assert r.text == "hi"
    finally:
        target.unlink()
