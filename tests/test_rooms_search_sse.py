"""Tests for multi-room, search, SSE hub, and long-poll."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from chatroom.hub import ChatroomHub
from chatroom.server import create_app
from chatroom.store import Store


@pytest.fixture
def mp_client(tmp_path):
    app = create_app(tmp_path, rooms=["main", "ops"])
    return TestClient(app), tmp_path


def _msg(tag: str, **kw) -> dict:
    base = {
        "from": tag,
        "type": "finding",
        "timestamp": "2026-09-14T15:00:00+08:00",
        "subject": f"{tag} hello world",
    }
    base.update(kw)
    return base


# --- multi-room ---
def test_api_rooms(mp_client):
    c, _ = mp_client
    assert c.get("/api/rooms").json() == {"rooms": ["main", "ops"]}


def test_room_isolation(mp_client):
    c, _ = mp_client
    assert c.post("/api/post?room=ops", json={"msg": _msg("human")}).status_code == 200
    ops = c.get("/api/messages?room=ops").json()["messages"]
    main = c.get("/api/messages?room=main").json()["messages"]
    assert len(ops) == 1 and ops[0]["room"] == "ops"
    assert main == []


def test_room_persisted_to_own_file(tmp_path):
    s = Store(tmp_path, "ops")
    s.append(_msg("human"))
    assert (tmp_path / "messages-ops.jsonl").exists()
    assert not (tmp_path / "messages.jsonl").exists()
    assert len(Store(tmp_path, "ops").read_all()) == 1


def test_default_room_stays_on_messages_jsonl(tmp_path):
    s = Store(tmp_path)
    s.append(_msg("human"))
    assert (tmp_path / "messages.jsonl").exists()


# --- search ---
def test_store_search_fields(tmp_path):
    s = Store(tmp_path)
    s.append(_msg("workbuddy", subject="targeting Q3 report"))
    s.append(_msg("opencode", subject="unrelated thing"))
    assert len(s.search("q3")) == 1
    assert len(s.search("thing")) == 1
    assert s.search("workbuddy")[0]["from"] == "workbuddy"
    assert s.search("report", field="subject")
    assert s.search("hello") == []  # not literal substring hit on that query


def test_api_search(mp_client):
    c, _ = mp_client
    c.post("/api/post", json={"msg": _msg("workbuddy", subject="find me please")})
    r = c.get("/api/search", params={"q": "find"})
    assert r.status_code == 200
    assert len(r.json()["messages"]) == 1
    r = c.get("/api/search", params={"q": "nope"})
    assert r.json() == {"messages": []}


def test_api_search_empty_query_rejected(mp_client):
    c, _ = mp_client
    assert c.get("/api/search", params={"q": ""}).status_code == 422


# --- hub SSE unit ---
@pytest.mark.asyncio
async def test_hub_sse_subscribe_broadcast():
    hub = ChatroomHub()
    q = await hub.sse_subscribe()
    await hub.broadcast({"kind": "message", "message": {"room": "main", "id": "msg-1"}})
    raw = await asyncio.wait_for(q.get(), timeout=1)
    assert "chat" not in raw  # sanity: it's the raw json payload
    import json

    assert json.loads(raw)["kind"] == "message"
    await hub.sse_unsubscribe(q)
    assert hub.client_count == 0


@pytest.mark.asyncio
async def test_hub_sse_unsubscribe_stops_delivery():
    hub = ChatroomHub()
    q = await hub.sse_subscribe()
    await hub.sse_unsubscribe(q)
    await hub.broadcast({"kind": "session_joined", "session": {}})
    try:
        await asyncio.wait_for(q.get(), timeout=0.05)
        pytest.fail("should not receive after unsubscribe")
    except TimeoutError:
        pass


# --- long-poll ---
def test_long_poll_returns_existing_right_away(mp_client):
    c, _ = mp_client
    c.post("/api/post", json={"msg": _msg("human", subject="first")})
    r = c.get("/api/messages/poll", params={"timeout": 5})
    assert r.status_code == 200
    assert [m["subject"] for m in r.json()["messages"]] == ["first"]


def test_long_poll_timeout_returns_empty(mp_client):
    c, _ = mp_client
    r = c.get("/api/messages/poll", params={"timeout": 0.05})
    assert r.status_code == 200
    assert r.json() == {"messages": []}


# --- SSE endpoint is registered (mechanism covered by hub unit tests; a live
# streaming connection would otherwise hold TestClient open indefinitely) ---
def test_sse_endpoint_registered(mp_client):
    c, _ = mp_client
    # Route exists: /api/stream returns 200 with event-stream media type on a
    # non-blocking check is not directly possible in sync TestClient; verify the
    # route is present by introspecting the app.
    routes = {getattr(r, "path", "") for r in c.app.routes}
    assert "/api/stream" in routes
