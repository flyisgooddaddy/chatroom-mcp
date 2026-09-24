"""Unit tests for chatroom_client.

Covers the three portable-from-TS pieces:
  * ChatroomClaim: atomic O_EXCL claim
  * AgentState: active marker + injection-echo guard
  * is_targeting: filter rules (to / @name / self-skip)

Plus config loading. The httpx transport and poll loop are deliberately
out of scope — they need a live chatroom server (covered by integration
tests under tests/integration/).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from chatroom_client.config import AgentConfig
from chatroom_client.core import (
    AgentState,
    ChatroomClaim,
    is_targeting,
)

# ----- ChatroomClaim --------------------------------------------------------


def test_claim_first_winner(tmp_path: Path) -> None:
    c = ChatroomClaim(tmp_path / "claims")
    assert c.try_acquire("msg-001") is True
    assert c.try_acquire("msg-001") is False


def test_claim_distinct_ids_are_independent(tmp_path: Path) -> None:
    c = ChatroomClaim(tmp_path / "claims")
    assert c.try_acquire("msg-001") is True
    assert c.try_acquire("msg-002") is True


def test_claim_concurrent_exactly_once(tmp_path: Path) -> None:
    """N threads racing on the same id → exactly one True."""
    c = ChatroomClaim(tmp_path / "claims")
    n_threads = 16
    results: list[bool] = []
    start = threading.Event()

    def worker() -> None:
        start.wait()
        results.append(c.try_acquire("msg-race"))

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join()

    assert results.count(True) == 1, f"expected exactly 1 winner, got {results.count(True)}"
    assert results.count(False) == n_threads - 1


def test_claim_prune_drops_old(tmp_path: Path) -> None:
    c = ChatroomClaim(tmp_path / "claims", retention_days=7)
    c.try_acquire("msg-old")
    old = tmp_path / "claims" / "msg-old"
    # backdate mtime by 10 days
    ten_days_ago = time.time() - 10 * 86400
    import os

    os.utime(old, (ten_days_ago, ten_days_ago))
    c.prune()
    assert not old.exists()


# ----- AgentState -----------------------------------------------------------


def test_state_active_roundtrip(tmp_path: Path) -> None:
    marker = tmp_path / "active"
    last = tmp_path / "lastinject"
    s = AgentState(marker, last)
    assert s.read_active() is None
    s.record_active("ses_abc123")
    assert s.read_active() == "ses_abc123"


def test_state_active_rejects_garbage(tmp_path: Path) -> None:
    marker = tmp_path / "active"
    last = tmp_path / "lastinject"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("garbage-no-sid-prefix\n", encoding="utf-8")
    s = AgentState(marker, last)
    assert s.read_active() is None


def test_state_injection_echo_within_window(tmp_path: Path) -> None:
    marker = tmp_path / "active"
    last = tmp_path / "lastinject"
    s = AgentState(marker, last)
    s.note_injection("ses_xyz")
    assert s.is_own_injection_echo("ses_xyz") is True


def test_state_injection_echo_outside_window(tmp_path: Path) -> None:
    marker = tmp_path / "active"
    last = tmp_path / "lastinject"
    s = AgentState(marker, last)
    last.parent.mkdir(parents=True, exist_ok=True)
    # 60s ago, well past ECHO_WINDOW_S = 20
    last.write_text(f"{int(time.time() * 1000) - 60_000} ses_xyz\n", encoding="utf-8")
    assert s.is_own_injection_echo("ses_xyz") is False


# ----- is_targeting ---------------------------------------------------------


def test_targeting_by_to_field() -> None:
    assert is_targeting({"from": "alice", "to": "opencode"}, ["opencode"]) is True


def test_targeting_by_at_in_subject() -> None:
    assert is_targeting(
        {"from": "alice", "to": "all", "subject": "FYI @opencode"},
        ["opencode"],
    ) is True


def test_targeting_by_at_in_body() -> None:
    assert is_targeting(
        {"from": "alice", "subject": "hi", "body": "hi @opencode please reply"},
        ["opencode"],
    ) is True


def test_targeting_skips_self() -> None:
    assert is_targeting({"from": "opencode", "to": "bob"}, ["opencode"]) is False


def test_targeting_alias_match() -> None:
    assert is_targeting({"from": "alice", "to": "opencode-desktop"}, ["opencode"]) is True


def test_targeting_unrelated_message() -> None:
    assert is_targeting(
        {"from": "alice", "to": "bob", "subject": "lunch?", "body": "want pizza?"},
        ["opencode"],
    ) is False


def test_targeting_empty_names_never_match() -> None:
    assert is_targeting({"from": "alice", "to": "opencode"}, []) is False


# ----- AgentConfig ----------------------------------------------------------


def test_config_from_yaml_happy(tmp_path: Path) -> None:
    yaml = tmp_path / "chatroom.yaml"
    yaml.write_text(
        "agent:\n  name: my-agent\n  aliases: [me]\n"
        "server:\n  url: http://127.0.0.1:7777\n  room: dev\n"
        "driver: opencode\n",
        encoding="utf-8",
    )
    cfg = AgentConfig.from_yaml(yaml)
    assert cfg.agent.name == "my-agent"
    assert cfg.agent.aliases == ["me"]
    assert cfg.server.url == "http://127.0.0.1:7777"
    assert cfg.server.room == "dev"
    assert cfg.all_names == ["my-agent", "me"]
    assert cfg.driver == "opencode"


def test_config_from_yaml_missing_required(tmp_path: Path) -> None:
    yaml = tmp_path / "chatroom.yaml"
    yaml.write_text("server:\n  url: http://x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="agent.name is required"):
        AgentConfig.from_yaml(yaml)


def test_config_from_yaml_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        AgentConfig.from_yaml(tmp_path / "nope.yaml")


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATROOM_AGENT_NAME", "envagent")
    monkeypatch.setenv("CHATROOM_HTTP", "http://h:9999")
    cfg = AgentConfig.from_env()
    assert cfg.agent.name == "envagent"
    assert cfg.server.url == "http://h:9999"
