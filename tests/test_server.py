"""HTTP-level tests for the chatroom-mcp server + Web GUI."""

import asyncio
import time as _time
from pathlib import Path

import pytest
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
    assert r.json() == {"messages": [], "total": 0}


def test_api_sessions_empty(client):
    c, _ = client
    r = c.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == {"agents": []}


def test_api_post_then_messages(client):
    c, _ = client
    payload = {
        "msg": {
            "from": "human",
            "type": "finding",
            "timestamp": "2026-09-14T15:00:00+08:00",
            "subject": "hello gui",
        }
    }
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


def test_kick_unregisters(client):
    c, _tmp = client
    reg = c.app.state.sessions
    reg.handshake("workbuddy", callback_url="http://x", bound_session_id="h1")
    r = c.delete("/api/sessions/workbuddy")
    assert r.status_code == 200
    assert r.json() == {"removed": "workbuddy", "banned": True}
    assert reg.get_agent("workbuddy") is None


def test_kick_missing_returns_404(client):
    c, _ = client
    r = c.delete("/api/sessions/never-existed")
    assert r.status_code == 404


def test_set_active_then_remove_host(client):
    """Multi-host model: set active, then kick one host without removing the agent."""
    c, _ = client
    reg = c.app.state.sessions
    reg.handshake(
        "opencode", callback_url="http://A", bound_session_id="ses_A", session_name="上午"
    )
    reg.handshake(
        "opencode", callback_url="http://B", bound_session_id="ses_B", session_name="晚间"
    )
    # switch active to A
    r = c.post("/api/agents/opencode/active", json={"sid": "ses_A"})
    assert r.status_code == 200
    assert r.json()["active_sid"] == "ses_A"
    # remove B (kick + ban that specific sid)
    r = c.delete("/api/agents/opencode/hosts/ses_B")
    assert r.status_code == 200
    assert r.json() == {"removed": {"agent": "opencode", "sid": "ses_B"}, "banned": True}
    # agent still alive with A
    reg2 = c.app.state.sessions
    a = reg2.get_agent("opencode")
    assert a is not None
    assert len(a.hosts) == 1
    assert a.active_sid == "ses_A"


def test_set_active_rejects_unknown_sid(client):
    c, _ = client
    c.app.state.sessions.handshake("opencode", callback_url="http://x", bound_session_id="ses_A")
    r = c.post("/api/agents/opencode/active", json={"sid": "ses_ZZZ"})
    assert r.status_code == 404


def test_remove_host_missing_returns_404(client):
    c, _ = client
    r = c.delete("/api/agents/never/hosts/never")
    assert r.status_code == 404


def test_kick_persists_via_banlist(client, tmp_path):
    """End-to-end: kick -> ban persists in _sessions.json -> fresh registry
    (simulating server restart) still rejects handshake."""
    c, _tmp = client
    reg = c.app.state.sessions
    reg.handshake(
        "opencode", callback_url="http://x", bound_session_id="ses_A", session_name="上午"
    )
    r = c.delete("/api/sessions/opencode")
    assert r.status_code == 200
    # fresh registry from same dir
    from chatroom.sessions import AgentRegistry

    fresh = AgentRegistry(tmp_path)
    assert fresh.get_agent("opencode") is None
    assert fresh.is_banned("opencode") is True
    # bridge retries handshake -> rejected
    with pytest.raises(ValueError, match="banned"):
        fresh.handshake("opencode", bound_session_id="ses_A", callback_url="http://x")


def test_kick_host_persists_via_banlist(client, tmp_path):
    """Per-host kick (DELETE /api/agents/{name}/hosts/{sid}) bans only that sid;
    other sids under the same name still register fine."""
    c, _tmp = client
    reg = c.app.state.sessions
    reg.handshake("opencode", callback_url="http://A", bound_session_id="ses_A")
    reg.handshake("opencode", callback_url="http://B", bound_session_id="ses_B")
    r = c.delete("/api/agents/opencode/hosts/ses_A")
    assert r.status_code == 200
    from chatroom.sessions import AgentRegistry

    fresh = AgentRegistry(tmp_path)
    # ses_A banned, ses_B free
    assert fresh.is_banned("opencode", "ses_A") is True
    assert fresh.is_banned("opencode", "ses_B") is False
    # ses_B can register
    fresh.handshake("opencode", bound_session_id="ses_B", callback_url="http://B")
    # ses_A still rejected
    with pytest.raises(ValueError, match="banned"):
        fresh.handshake("opencode", bound_session_id="ses_A", callback_url="http://A")


def test_api_bans_lists_banned_entries(client):
    """GET /api/bans surfaces the persistent ban list for visibility."""
    c, _ = client
    reg = c.app.state.sessions
    reg.handshake("opencode", callback_url="http://x", bound_session_id="ses_A")
    c.delete("/api/sessions/opencode")  # bans (opencode, None)
    r = c.get("/api/bans")
    assert r.status_code == 200
    body = r.json()
    assert body == {"bans": [{"name": "opencode", "sid": None}]}


def test_api_sessions_lazy_gc(client):
    c, _tmp = client
    reg = c.app.state.sessions
    reg.handshake("stale", callback_url="http://x", bound_session_id="h-s")
    a = reg.get_agent("stale")
    a.hosts[0].last_seen = _time.time() - 999
    reg._save()
    r = c.get("/api/sessions")
    assert r.status_code == 200
    assert all(s["name"] != "stale" for s in r.json()["agents"])
    assert reg.get_agent("stale") is None


def test_push_blocked_when_agent_has_no_live_host(client, monkeypatch):
    """@target whose agent has no live host (all stale or empty): push must NOT fire."""
    c, _tmp = client
    reg = c.app.state.sessions
    reg.handshake("workbuddy", callback_url="http://x", bound_session_id="h1")
    a = reg.get_agent("workbuddy")
    a.hosts[0].last_seen = _time.time() - 999  # make host stale
    reg._save()
    called = []

    async def fake_notify(url, payload, *a, **kw):
        called.append(url)
        return True

    from chatroom import push

    monkeypatch.setattr(push, "notify", fake_notify)
    r = c.post(
        "/api/post",
        json={
            "msg": {
                "from": "human",
                "type": "finding",
                "timestamp": "2026-09-14T15:00:00+08:00",
                "subject": "hi @workbuddy",
                "to": "workbuddy",
            }
        },
    )
    assert r.status_code == 200
    assert called == []


def test_push_routes_to_active_host_only(client, monkeypatch):
    """When two hosts register under one agent, push only goes to the active one."""
    c, _tmp = client
    reg = c.app.state.sessions
    reg.handshake("opencode", callback_url="http://A", bound_session_id="ses_A")
    reg.handshake("opencode", callback_url="http://B", bound_session_id="ses_B")
    # active is B (latest handshake)
    called = []

    async def fake_notify(url, payload, *a, **kw):
        called.append(url)
        return True

    from chatroom import push

    monkeypatch.setattr(push, "notify", fake_notify)
    c.post(
        "/api/post",
        json={
            "msg": {
                "from": "human",
                "type": "finding",
                "timestamp": "2026-09-14T15:00:00+08:00",
                "subject": "@opencode",
                "to": "opencode",
            }
        },
    )
    assert called == ["http://B"]
    # switch active and re-test
    c.post("/api/agents/opencode/active", json={"sid": "ses_A"})
    called.clear()
    c.post(
        "/api/post",
        json={
            "msg": {
                "from": "human",
                "type": "finding",
                "timestamp": "2026-09-14T15:00:01+08:00",
                "subject": "@opencode again",
                "to": "opencode",
            }
        },
    )
    assert called == ["http://A"]


def test_push_routes_by_session_name(client, monkeypatch):
    """@<session_name> routes to that specific host, even if it is not the active one."""
    c, _tmp = client
    reg = c.app.state.sessions
    # Two hosts, active = B (latest). Host A has a distinct session_name.
    reg.handshake(
        "opencode", callback_url="http://A", bound_session_id="ses_A", session_name="上午PE"
    )
    reg.handshake(
        "opencode", callback_url="http://B", bound_session_id="ses_B", session_name="晚间修bug"
    )
    assert reg.get_agent("opencode").active_sid == "ses_B"
    called = []

    async def fake_notify(url, payload, *a, **kw):
        called.append(url)
        return True

    from chatroom import push

    monkeypatch.setattr(push, "notify", fake_notify)
    # Target by session_name (上午PE) — should hit A, not the active B.
    c.post(
        "/api/post",
        json={
            "msg": {
                "from": "human",
                "type": "finding",
                "timestamp": "2026-09-14T15:00:00+08:00",
                "subject": "@上午PE",
                "to": "上午PE",
            }
        },
    )
    assert called == ["http://A"]


def test_push_agent_name_wins_on_collision(client, monkeypatch):
    """If a session_name equals an agent name, the agent lookup takes precedence and
    routes to the agent's active host (not a lookalike host)."""
    c, _tmp = client
    reg = c.app.state.sessions
    # Agent "opencode" with active host B; a lookalike host ALSO named "opencode".
    reg.handshake("opencode", callback_url="http://B", bound_session_id="ses_B")
    reg.handshake(
        "opencode", callback_url="http://A", bound_session_id="ses_A", session_name="opencode"
    )
    reg.set_active("opencode", "ses_B")
    called = []

    async def fake_notify(url, payload, *a, **kw):
        called.append(url)
        return True

    from chatroom import push

    monkeypatch.setattr(push, "notify", fake_notify)
    c.post(
        "/api/post",
        json={
            "msg": {
                "from": "human",
                "type": "finding",
                "timestamp": "2026-09-14T15:00:00+08:00",
                "subject": "@opencode",
                "to": "opencode",
            }
        },
    )
    assert called == ["http://B"]  # agent name wins → active host


def test_push_session_name_unknown_is_dropped(client, monkeypatch):
    """Unknown target (no agent, no host session_name) → push silently dropped."""
    c, _tmp = client
    called = []

    async def fake_notify(url, payload, *a, **kw):
        called.append(url)
        return True

    from chatroom import push

    monkeypatch.setattr(push, "notify", fake_notify)
    c.post(
        "/api/post",
        json={
            "msg": {
                "from": "human",
                "type": "finding",
                "timestamp": "2026-09-14T15:00:00+08:00",
                "subject": "@ghost",
                "to": "ghost",
            }
        },
    )
    assert called == []


def test_probe_one_refreshes_last_seen_on_2xx(client, monkeypatch):
    """Chatroom actively probes the active host's callback_url; on 2xx, last_seen refreshed."""
    c, _tmp = client
    reg = c.app.state.sessions
    reg.handshake("workbuddy", callback_url="http://probe-target", bound_session_id="h1")
    a = reg.get_agent("workbuddy")
    a.hosts[0].last_seen = _time.time() - 10  # within SESSION_TIMEOUT=90
    active = a.active_host
    assert active is not None, "test precondition: host must be within TTL"

    class _FakeResp:
        status_code = 200

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _FakeResp()

    from chatroom import server

    monkeypatch.setattr(server.httpx, "AsyncClient", _FakeClient)
    ok = asyncio.run(server._probe_one(reg, "workbuddy", active.sid, active.callback_url))
    assert ok is True
    refreshed = reg.get_agent("workbuddy").hosts[0].last_seen
    assert refreshed > _time.time() - 10


def test_probe_one_returns_false_on_failure(client, monkeypatch):
    """Failed probe does NOT refresh last_seen — TTL GC will eventually drop it."""
    c, _tmp = client
    reg = c.app.state.sessions
    reg.handshake("workbuddy", callback_url="http://dead", bound_session_id="h1")
    a = reg.get_agent("workbuddy")
    stale_before = _time.time() - 10  # within TTL — host is live
    a.hosts[0].last_seen = stale_before
    reg._save()
    active = a.active_host
    assert active is not None

    class _BoomClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            raise ConnectionError("dead")

    from chatroom import server

    monkeypatch.setattr(server.httpx, "AsyncClient", _BoomClient)
    ok = asyncio.run(server._probe_one(reg, "workbuddy", active.sid, active.callback_url))
    assert ok is False
    assert abs(reg.get_agent("workbuddy").hosts[0].last_seen - stale_before) < 1


def test_static_dir(client):
    from chatroom.server import WEB_DIR

    static = WEB_DIR / "static"
    static.mkdir(parents=True, exist_ok=True)
    target = static / "test_tmp.txt"
    target.write_text("hi", encoding="utf-8")
    try:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            app = create_app(Path(td))
            c = TestClient(app)
            r = c.get("/static/test_tmp.txt")
            assert r.status_code == 200
            assert r.text == "hi"
    finally:
        target.unlink()


def test_api_post_merges_body_mentions_into_to(client):
    """方案1: server 落库时把 body @-mention(能解析的)并入 msg.to.

    PUSH agents only get notified when they appear in `to`; body @ was dropped
    for them. Fix merges resolvable body mentions into to, de-dup, keep case,
    and ignores non-agent @ tokens / emails.
    """
    c, tmp = client
    sessions = c.app.state.sessions
    sessions.handshake("fakeagent", "ses_fake_1", callback_url="http://127.0.0.1:9/x", machine="m")

    def post(body, to):
        payload = {"msg": {"from": "human", "type": "finding", "timestamp": "2026-09-25T12:00:00+08:00",
                          "subject": "t", "body": body, "to": to}}
        r = c.post("/api/post", json=payload)
        assert r.status_code == 200, r.text
        return r.json()

    # 1) body @fakeagent + existing to -> to 有 fakeagent; @nobody(非agent)过滤; 保留大小写
    stored = post("@fakeagent, 你好 @nobody", "human")
    to = stored.get("to")
    toset = set(to if isinstance(to, list) else [to])
    assert "fakeagent" in toset
    assert "nobody" not in toset

    # 2) email 不被误当 mention(example.com resolve 失败, skip)
    stored = post("user@example.com 测试 @fakeagent", None)
    to = stored.get("to")
    toset = set(to if isinstance(to, list) else [to])
    assert "fakeagent" in toset
    assert not any("example" in t for t in toset)

    # 3) 短 session_name 也并入(resolve ok)
    sessions.handshake("ses_short", "ses_short_1", callback_url="http://127.0.0.1:9/y", machine="m")
    stored = post("@ses_short @fakeagent", "")
    to = stored.get("to")
    toset = set(to if isinstance(to, list) else [to])
    assert {"ses_short", "fakeagent"} <= toset
