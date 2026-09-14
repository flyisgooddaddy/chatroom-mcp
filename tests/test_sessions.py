"""Smoke tests for SessionRegistry."""
import time
from pathlib import Path

from chatroom.sessions import SessionRegistry


def test_first_handshake(tmp_path: Path):
    r = SessionRegistry(tmp_path)
    s = r.handshake("workbuddy", callback_url="http://x")
    assert s.name == "workbuddy"
    assert s.session_id
    assert s.callback_url == "http://x"
    assert s.status == "online"


def test_handshake_refresh(tmp_path: Path):
    r = SessionRegistry(tmp_path)
    s1 = r.handshake("opencode")
    s2 = r.handshake("opencode", callback_url="http://new")
    assert s1.session_id == s2.session_id  # same session
    assert s2.callback_url == "http://new"


def test_persistence(tmp_path: Path):
    r1 = SessionRegistry(tmp_path)
    r1.handshake("workbuddy", callback_url="http://a")
    r2 = SessionRegistry(tmp_path)
    s = r2.get("workbuddy")
    assert s is not None
    assert s.callback_url == "http://a"


def test_mark_offline(tmp_path: Path):
    r = SessionRegistry(tmp_path)
    r.handshake("workbuddy")
    r.mark_offline("workbuddy")
    assert r.get("workbuddy").status == "offline"


def test_all_returns_list(tmp_path: Path):
    r = SessionRegistry(tmp_path)
    r.handshake("a")
    r.handshake("b")
    names = {s.name for s in r.all()}
    assert names == {"a", "b"}
