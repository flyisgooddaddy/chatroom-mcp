"""Tests for AgentRegistry (multi-host per agent name)."""

import json
import time
from pathlib import Path

import pytest

from chatroom.sessions import (
    MAX_HOSTS_PER_AGENT,
    AgentRegistry,
)

BOUND = "host-session-1"
CB = "http://callback"


def test_first_handshake_creates_agent_with_one_host(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    a = r.handshake("workbuddy", bound_session_id=BOUND, callback_url=CB, session_name="first")
    assert a.name == "workbuddy"
    assert a.active_sid == BOUND
    assert len(a.hosts) == 1
    h = a.hosts[0]
    assert h.sid == BOUND
    assert h.session_name == "first"
    assert h.callback_url == CB
    assert h.status == "online"


def test_handshake_same_sid_refreshes_existing_host(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("workbuddy", bound_session_id=BOUND, callback_url=CB)
    a = r.handshake(
        "workbuddy", bound_session_id=BOUND, callback_url="http://new", session_name="renamed"
    )
    assert len(a.hosts) == 1  # not duplicated
    assert a.hosts[0].callback_url == "http://new"
    assert a.hosts[0].session_name == "renamed"


def test_handshake_different_sid_adds_new_host(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    a1 = r.handshake("opencode", bound_session_id="ses_A", callback_url=CB, session_name="上午")
    a2 = r.handshake(
        "opencode", bound_session_id="ses_B", callback_url="http://b", session_name="晚间"
    )
    assert a1.name == a2.name == "opencode"
    assert {h.sid for h in a1.hosts} == {"ses_A", "ses_B"}
    # latest handshake sets active
    assert a2.active_sid == "ses_B"


def test_set_active_switches_push_target(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url="http://A")
    r.handshake("opencode", bound_session_id="ses_B", callback_url="http://B")
    a = r.set_active("opencode", "ses_A")
    assert a.active_sid == "ses_A"
    assert a.active_host.sid == "ses_A"
    assert a.active_host.callback_url == "http://A"


def test_set_active_rejects_unknown_sid(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    with pytest.raises(ValueError, match="not a host"):
        r.set_active("opencode", "ses_ZZZ")


def test_set_active_rejects_unknown_agent(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    with pytest.raises(ValueError, match="unknown agent"):
        r.set_active("nope", "ses_A")


def test_remove_host_picks_new_active(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    r.handshake("opencode", bound_session_id="ses_B", callback_url=CB)
    r.set_active("opencode", "ses_B")
    r.remove_host("opencode", "ses_B")
    a = r.get_agent("opencode")
    assert a is not None
    assert a.active_sid == "ses_A"


def test_remove_last_host_drops_agent(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    assert r.remove_host("opencode", "ses_A") is True
    assert r.get_agent("opencode") is None


def test_remove_host_unknown_returns_false(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    assert r.remove_host("nope", "ses_A") is False
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    assert r.remove_host("opencode", "ses_B") is False


def test_unregister_removes_whole_agent(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("workbuddy", bound_session_id="ses_A", callback_url=CB)
    r.handshake("workbuddy", bound_session_id="ses_B", callback_url=CB)
    assert r.unregister("workbuddy") is True
    assert r.get_agent("workbuddy") is None
    assert r.unregister("workbuddy") is False


def test_handshake_requires_name(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    with pytest.raises(ValueError, match="name"):
        r.handshake("", bound_session_id=BOUND, callback_url=CB)


def test_handshake_requires_bound_session_id(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    with pytest.raises(ValueError, match="bound_session_id"):
        r.handshake("workbuddy", bound_session_id="", callback_url=CB)


def test_handshake_allows_empty_callback_url(tmp_path: Path):
    """callback_url is optional — pull-only agents register with no callback."""
    r = AgentRegistry(tmp_path)
    a = r.handshake("workbuddy", bound_session_id=BOUND, callback_url="")
    assert a.hosts[0].callback_url == ""
    assert r.get_agent("workbuddy") is not None


def test_persistence_round_trip(tmp_path: Path):
    r1 = AgentRegistry(tmp_path)
    r1.handshake("workbuddy", bound_session_id="ses_A", callback_url="http://a", session_name="A")
    r1.handshake("opencode", bound_session_id="ses_X", callback_url="http://x", session_name="X")
    r1.handshake("opencode", bound_session_id="ses_Y", callback_url="http://y", session_name="Y")
    r2 = AgentRegistry(tmp_path)
    assert r2.get_agent("workbuddy").hosts[0].session_name == "A"
    assert {h.sid for h in r2.get_agent("opencode").hosts} == {"ses_X", "ses_Y"}


def test_legacy_migration(tmp_path: Path):
    """Old flat _sessions.json gets migrated to grouped agents on first load."""
    legacy = {
        "sessions": [
            {
                "name": "opencode",
                "session_id": "ses_X",
                "callback_url": "http://x",
                "bound_session_id": "ses_X",
                "last_seen": 100.0,
                "status": "online",
                "session_name": "上午",
            },
            {
                "name": "opencode",
                "session_id": "ses_Y",
                "callback_url": "http://y",
                "bound_session_id": "ses_Y",
                "last_seen": 200.0,
                "status": "online",
            },
            {
                "name": "workbuddy",
                "session_id": "ses_A",
                "callback_url": "http://a",
                "bound_session_id": "ses_A",
                "last_seen": 150.0,
                "status": "online",
            },
        ]
    }
    p = tmp_path / "_sessions.json"
    p.write_text(json.dumps(legacy), encoding="utf-8")
    r = AgentRegistry(tmp_path)
    opencode = r.get_agent("opencode")
    assert len(opencode.hosts) == 2
    assert {h.sid for h in opencode.hosts} == {"ses_X", "ses_Y"}
    # last_seen wins as active after migration
    assert opencode.active_sid == "ses_Y"
    workbuddy = r.get_agent("workbuddy")
    assert workbuddy.active_sid == "ses_A"


def test_max_hosts_per_agent_enforced(tmp_path: Path):
    """When live hosts hit the cap, handshake of a new host fails until GC."""
    r = AgentRegistry(tmp_path)
    for i in range(MAX_HOSTS_PER_AGENT):
        r.handshake("opencode", bound_session_id=f"ses_{i}", callback_url=CB)
    with pytest.raises(ValueError, match="live hosts"):
        r.handshake("opencode", bound_session_id="ses_overflow", callback_url=CB)


def test_max_hosts_evicts_stale(tmp_path: Path):
    """A new handshake at the cap evicts the oldest OFFLINE host to make room."""
    r = AgentRegistry(tmp_path)
    for i in range(MAX_HOSTS_PER_AGENT):
        r.handshake("opencode", bound_session_id=f"ses_{i}", callback_url=CB)
    # Force one host offline by setting last_seen far past SESSION_TIMEOUT
    a = r.get_agent("opencode")
    a.hosts[0].last_seen = time.time() - 999
    a.hosts[0].status = "offline"
    a.hosts[0].effective_status.cache_clear() if hasattr(
        a.hosts[0].effective_status, "cache_clear"
    ) else None
    r._save()
    # New handshake should succeed (evicts the stale one)
    a2 = r.handshake("opencode", bound_session_id="ses_new", callback_url=CB)
    sids = {h.sid for h in a2.hosts}
    assert "ses_0" not in sids  # evicted
    assert "ses_new" in sids
    assert len(a2.hosts) == MAX_HOSTS_PER_AGENT


def test_heartbeat_refreshes_last_seen(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    a = r.get_agent("opencode")
    a.hosts[0].last_seen = time.time() - 999
    r._save()
    assert r.heartbeat("opencode", "ses_A") is True
    assert r.get_agent("opencode").hosts[0].last_seen > time.time() - 5


def test_kicked_host_not_resurrected_by_heartbeat(tmp_path: Path):
    """Kick removes from the registry (source of truth); a subsequent heartbeat
    from that sid must NOT re-create it — heartbeat only refreshes, it does not
    establish."""
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    assert r.remove_host("opencode", "ses_A") is True
    assert r.get_agent("opencode") is None
    # bridge still heartbeating its (now-kicked) host
    assert r.heartbeat("opencode", "ses_A") is False
    assert r.get_agent("opencode") is None


def test_heartbeat_unknown_returns_false(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    assert r.heartbeat("nope", "ses_A") is False
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    assert r.heartbeat("opencode", "ses_Z") is False


def test_gc_drops_stale_hosts(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    r.handshake("opencode", bound_session_id="ses_B", callback_url=CB)
    a = r.get_agent("opencode")
    a.hosts[0].last_seen = time.time() - 999
    removed = r.gc()
    assert ("opencode", "ses_A") in removed
    assert r.get_agent("opencode").hosts[0].sid == "ses_B"


def test_gc_drops_whole_agent_when_all_stale(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    r.get_agent("opencode").hosts[0].last_seen = time.time() - 999
    r.gc()
    assert r.get_agent("opencode") is None


def test_gc_repairs_active_after_stale_eviction(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    r.handshake("opencode", bound_session_id="ses_B", callback_url=CB)
    r.set_active("opencode", "ses_A")
    r.get_agent("opencode").hosts[0].last_seen = time.time() - 999  # ses_A stale
    r.gc()
    a = r.get_agent("opencode")
    assert a is not None
    assert a.active_sid == "ses_B"  # repaired to most-recent


def test_probeable_active_hosts(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url="http://A")
    r.handshake("opencode", bound_session_id="ses_B", callback_url="http://B")
    pairs = r.probeable_active_hosts()
    assert len(pairs) == 1
    name, host = pairs[0]
    assert name == "opencode"
    assert host.sid == "ses_B"  # latest handshake wins as active


def test_probeable_hosts_returns_all_hosts(tmp_path: Path):
    """Liveness probe covers EVERY host, not just the active one, so all
    registered sessions stay alive (probed individually)."""
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url="http://A")
    r.handshake("opencode", bound_session_id="ses_B", callback_url="http://B")
    r.handshake("opencode", bound_session_id="ses_C", callback_url="http://C")
    pairs = r.probeable_hosts()
    assert {host.sid for _, host in pairs} == {"ses_A", "ses_B", "ses_C"}


def test_active_host_fallback_to_most_recent(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    # manually clear active_sid to test fallback
    r.get_agent("opencode").active_sid = None
    a = r.get_agent("opencode")
    assert a.active_host.sid == "ses_A"


def test_mark_offline(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    r.mark_offline("opencode", "ses_A")
    assert r.get_agent("opencode").hosts[0].status == "offline"


def test_all_agents_returns_list(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("a", bound_session_id="x", callback_url=CB)
    r.handshake("b", bound_session_id="y", callback_url=CB)
    names = {a.name for a in r.all_agents()}
    assert names == {"a", "b"}


def test_to_dict_shape(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB, session_name="上午PE")
    d = r.get_agent("opencode").to_dict()
    assert set(d.keys()) == {"name", "active_sid", "hosts"}
    assert d["active_sid"] == "ses_A"
    assert len(d["hosts"]) == 1
    h = d["hosts"][0]
    assert h["sid"] == "ses_A"
    assert h["session_name"] == "上午PE"
    assert h["status"] == "online"  # effective_status in to_dict


# ---- ban list (persistent kick) ----


def test_ban_name_removes_agent_and_persists(tmp_path: Path):
    """ban(name) removes the agent and adds (name, None) to the ban list,
    both in-memory and on disk so it survives a server restart."""
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    assert r.ban("opencode") is True
    assert r.get_agent("opencode") is None
    assert r.is_banned("opencode") is True
    r2 = AgentRegistry(tmp_path)
    assert r2.get_agent("opencode") is None
    assert r2.is_banned("opencode") is True


def test_ban_sid_removes_only_that_host(tmp_path: Path):
    """ban(name, sid) removes just that host; other sids under the same name
    are untouched (and not banned)."""
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB, session_name="A")
    r.handshake("opencode", bound_session_id="ses_B", callback_url=CB, session_name="B")
    assert r.ban("opencode", "ses_A") is True
    a = r.get_agent("opencode")
    assert a is not None
    assert [h.sid for h in a.hosts] == ["ses_B"]
    assert r.is_banned("opencode", "ses_A") is True
    assert r.is_banned("opencode", "ses_B") is False


def test_ban_idempotent_returns_false_on_repeat(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    assert r.ban("opencode") is True
    assert r.ban("opencode") is False  # already banned, no new entry


def test_handshake_after_ban_raises(tmp_path: Path):
    """After ban, the bridge's re-handshake must NOT silently re-register —
    it raises ValueError so the MCP tool returns an error and the bridge
    sees a clear failure."""
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    r.ban("opencode", "ses_A")
    with pytest.raises(ValueError, match="banned"):
        r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)


def test_handshake_after_name_ban_raises_for_any_sid(tmp_path: Path):
    """ban(name) without a sid covers ALL sids for that name."""
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    r.ban("opencode")
    with pytest.raises(ValueError, match="banned"):
        r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    with pytest.raises(ValueError, match="banned"):
        r.handshake("opencode", bound_session_id="ses_NEVER_SEEN", callback_url=CB)


def test_unban_removes_specific_sid_only(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    r.handshake("opencode", bound_session_id="ses_B", callback_url=CB)
    r.ban("opencode", "ses_A")
    r.ban("opencode", "ses_B")
    assert r.unban("opencode", "ses_A") is True
    assert r.is_banned("opencode", "ses_A") is False
    assert r.is_banned("opencode", "ses_B") is True
    # ses_A can register again
    a = r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    assert any(h.sid == "ses_A" for h in a.hosts)


def test_unban_name_clears_all_sids_under_that_name(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    r.ban("opencode")
    r.ban("opencode", "ses_A")  # explicit per-sid, redundant but legal
    assert r.unban("opencode") is True
    assert r.is_banned("opencode") is False
    assert r.is_banned("opencode", "ses_A") is False


def test_unban_unknown_returns_false(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    assert r.unban("never-banned") is False
    assert r.unban("", "x") is False


def test_banned_lists_sorted(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    r.ban("zeta", "ses_Z")
    r.ban("alpha", "ses_A")
    r.ban("alpha")  # whole-name ban
    out = r.banned()
    assert out == [("alpha", None), ("alpha", "ses_A"), ("zeta", "ses_Z")]


def test_kicked_host_not_resurrected_by_handshake(tmp_path: Path):
    """End-to-end regression: bridge calls handshake every ~30s; after ban,
    repeated handshake must NOT bring the host back."""
    r = AgentRegistry(tmp_path)
    r.handshake("opencode", bound_session_id="ses_A", callback_url=CB, session_name="上午")
    r.ban("opencode", "ses_A")
    assert r.get_agent("opencode") is None
    # simulate bridge retrying 5 times
    for _ in range(5):
        with pytest.raises(ValueError, match="banned"):
            r.handshake("opencode", bound_session_id="ses_A", callback_url=CB)
    assert r.get_agent("opencode") is None


def test_ban_requires_name(tmp_path: Path):
    r = AgentRegistry(tmp_path)
    with pytest.raises(ValueError, match="name"):
        r.ban("")
