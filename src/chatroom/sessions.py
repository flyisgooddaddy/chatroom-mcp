"""Agent registry: multi-host per agent name with explicit active binding.

Storage shape (post-migration):
    agents: [
      {
        name: "opencode",
        active_sid: "ses_xxx",          # currently-bound host (push target)
        hosts: [
          { sid, session_name, callback_url, last_seen, status },
          ...
        ],
      },
      ...
    ]
    bans: [
      { name: "opencode", sid: "ses_xxx" or null },
      ...
    ]
    A ban entry with sid=null blocks the WHOLE name (any sid handshake rejected).
    A ban entry with a concrete sid blocks only that specific (name, sid).

Legacy `_load()` migrates the previous flat shape:
    sessions: [{name, session_id, callback_url, bound_session_id, last_seen, status}]
into the grouped form on first read.

Kick semantics (post-ban):
    kick = ban + remove. The registry is the source of truth; if a banned host's
    adapter keeps calling handshake, the call is rejected with ValueError so the
    bridge sees a clear failure and stops polling. The server's 30s probe loop
    only iterates registered hosts, so a kicked/removed host is not probed.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SESSION_TIMEOUT = 90.0
MAX_HOSTS_PER_AGENT = 32


@dataclass
class HostSession:
    """One opencode session that registered as a host of an agent name."""

    sid: str  # opencode session id (== bound_session_id on the wire)
    session_name: str  # human label shown in UI
    callback_url: str  # chatroom pushes @-mentions here
    last_seen: float
    status: str = "online"
    machine: str = ""  # machine label (e.g. "openwriter-bobo-T300LA"); empty for legacy hosts

    def effective_status(self, timeout: float = SESSION_TIMEOUT) -> str:
        if self.status == "offline":
            return "offline"
        if time.time() - self.last_seen > timeout:
            return "offline"
        return self.status or "online"


@dataclass
class Agent:
    name: str
    hosts: list[HostSession] = field(default_factory=list)
    active_sid: str | None = None

    @property
    def active_host(self) -> HostSession | None:
        if self.active_sid:
            for h in self.hosts:
                if h.sid == self.active_sid and h.effective_status() == "online":
                    return h
        live = [h for h in self.hosts if h.effective_status() == "online"]
        if not live:
            return None
        return max(live, key=lambda h: h.last_seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "active_sid": self.active_sid,
            "hosts": [{**asdict(h), "status": h.effective_status()} for h in self.hosts],
        }


def _migrate_legacy(data: dict[str, Any]) -> dict[str, Any]:
    """Convert old flat sessions[] into new grouped agents[]."""
    out: dict[str, Any] = {"agents": []}
    by_name: dict[str, dict[str, Any]] = {}
    for s in data.get("sessions", []) or []:
        name = s.get("name")
        if not name:
            continue
        if name not in by_name:
            entry = {"name": name, "active_sid": None, "hosts": []}
            by_name[name] = entry
            out["agents"].append(entry)
        host = {
            "sid": s.get("bound_session_id") or s.get("session_id") or "",
            "session_name": s.get("session_name") or "",
            "callback_url": s.get("callback_url") or "",
            "last_seen": s.get("last_seen", time.time()),
            "status": s.get("status", "online"),
        }
        by_name[name]["hosts"].append(host)
    # Active = most recently seen host per agent (matches "latest wins" policy).
    for entry in out["agents"]:
        live = [h for h in entry["hosts"] if h["sid"]]
        if live:
            entry["active_sid"] = max(live, key=lambda h: h["last_seen"])["sid"]
    return out


class AgentRegistry:
    def __init__(self, comms_dir: Path) -> None:
        self.comms_dir = Path(comms_dir)
        self.path = self.comms_dir / "_sessions.json"
        self._agents: dict[str, Agent] = {}
        # Ban set: (name, sid_or_None). sid=None means the WHOLE name is banned
        # (any sid handshake rejected). Persisted in _sessions.json under "bans".
        self._bans: set[tuple[str, str | None]] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if "agents" not in data and "sessions" in data:
            data = _migrate_legacy(data)
        for a in data.get("agents", []) or []:
            try:
                hosts = [
                    HostSession(
                        sid=h["sid"],
                        session_name=h.get("session_name", ""),
                        callback_url=h.get("callback_url", ""),
                        last_seen=h.get("last_seen", time.time()),
                        status=h.get("status", "online"),
                    )
                    for h in a.get("hosts", [])
                    if h.get("sid")
                ]
            except (KeyError, TypeError):
                continue
            if not hosts:
                continue
            self._agents[a["name"]] = Agent(
                name=a["name"],
                hosts=hosts,
                active_sid=a.get("active_sid"),
            )
        for b in data.get("bans", []) or []:
            name = b.get("name")
            if not name:
                continue
            sid = b.get("sid") or None
            self._bans.add((name, sid))

    def _save(self) -> None:
        data = {
            "agents": [a.to_dict() for a in self._agents.values()],
            "bans": [
                {"name": name, "sid": sid}
                for (name, sid) in sorted(self._bans, key=lambda ns: (ns[0], ns[1] or ""))
            ],
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def handshake(
        self,
        name: str,
        bound_session_id: str,
        callback_url: str = "",
        session_name: str | None = None,
        machine: str = "",
    ) -> Agent:
        if not name:
            raise ValueError("missing name (register contract)")
        if not bound_session_id:
            raise ValueError("missing bound_session_id (register contract)")
        if self.is_banned(name, bound_session_id):
            raise ValueError(
                f"agent {name!r} (sid {bound_session_id!r}) is banned; "
                f"remove the ban before handshake can succeed"
            )
        now = time.time()
        agent = self._agents.setdefault(name, Agent(name=name))
        existing = next((h for h in agent.hosts if h.sid == bound_session_id), None)
        if existing:
            existing.last_seen = now
            existing.status = "online"
            existing.callback_url = callback_url
            if session_name:
                existing.session_name = session_name
            if machine:
                existing.machine = machine
        else:
            if len(agent.hosts) >= MAX_HOSTS_PER_AGENT:
                evicted = self._evict_stale_host(agent)
                if not evicted:
                    raise ValueError(
                        f"agent {name!r} already has {MAX_HOSTS_PER_AGENT} live hosts; "
                        f"unregister or GC stale ones before adding more"
                    )
            agent.hosts.append(
                HostSession(
                    sid=bound_session_id,
                    session_name=session_name or f"{bound_session_id[:8]}",
                    callback_url=callback_url,
                    last_seen=now,
                    status="online",
                    machine=machine,
                )
            )
        # Auto-active: latest handshake wins. UI can override via set_active().
        agent.active_sid = bound_session_id
        self._save()
        return agent

    def _evict_stale_host(self, agent: Agent) -> bool:
        stale = [h for h in agent.hosts if h.effective_status() == "offline"]
        if not stale:
            return False
        victim = min(stale, key=lambda h: h.last_seen)
        agent.hosts.remove(victim)
        return True

    def heartbeat(self, name: str, sid: str) -> bool:
        agent = self._agents.get(name)
        if agent is None:
            return False
        host = next((h for h in agent.hosts if h.sid == sid), None)
        if host is None:
            return False
        host.last_seen = time.time()
        host.status = "online"
        self._save()
        return True

    def get_agent(self, name: str) -> Agent | None:
        return self._agents.get(name)

    def all_agents(self) -> list[Agent]:
        return list(self._agents.values())

    def set_active(self, name: str, sid: str) -> Agent:
        agent = self._agents.get(name)
        if agent is None:
            raise ValueError(f"unknown agent {name!r}")
        if not any(h.sid == sid for h in agent.hosts):
            raise ValueError(f"sid {sid!r} not a host of agent {name!r}")
        agent.active_sid = sid
        self._save()
        return agent

    def remove_host(self, name: str, sid: str) -> bool:
        agent = self._agents.get(name)
        if agent is None:
            return False
        before = len(agent.hosts)
        agent.hosts = [h for h in agent.hosts if h.sid != sid]
        if len(agent.hosts) == before:
            return False
        if agent.active_sid == sid:
            agent.active_sid = (
                max(agent.hosts, key=lambda h: h.last_seen).sid if agent.hosts else None
            )
        if not agent.hosts:
            del self._agents[name]
        self._save()
        return True

    def unregister(self, name: str) -> bool:
        """Remove the agent and all its hosts."""
        if name in self._agents:
            del self._agents[name]
            self._save()
            return True
        return False

    # ---- Ban list (persistent kick) ----

    def is_banned(self, name: str, sid: str | None = None) -> bool:
        """True if (name) or (name, sid) is in the ban list.

        `sid=None` is used by callers that don't have a sid (e.g. probe loop
        asking 'is this whole agent banned?'). With a real sid, this returns
        True if EITHER the whole name is banned OR that specific (name, sid)
        is banned.
        """
        if (name, None) in self._bans:
            return True
        if sid is not None and (name, sid) in self._bans:
            return True
        return False

    def banned(self) -> list[tuple[str, str | None]]:
        """Snapshot of the ban list (sorted for stable display)."""
        return sorted(self._bans, key=lambda ns: (ns[0], ns[1] or ""))

    def ban(self, name: str, sid: str | None = None) -> bool:
        """Add (name, sid) to the persistent ban list and remove the host
        (or whole agent if sid is None) from the live registry.

        Returns True if a NEW ban entry was created (idempotent on repeat).
        The caller is expected to surface this as the new "kick" semantics:
        once banned, the bridge's handshake is rejected, so the host stays
        disconnected across the adapter's re-registration loop.
        """
        if not name:
            raise ValueError("missing name (ban contract)")
        key = (name, sid or None)
        added = key not in self._bans
        self._bans.add(key)
        if sid:
            self.remove_host(name, sid)
        else:
            self.unregister(name)
        if added:
            self._save()
        return added

    def unban(self, name: str, sid: str | None = None) -> bool:
        """Remove (name, sid) from the ban list. With sid=None, removes every
        ban entry under that name (whole-name AND per-sid bans). Idempotent."""
        if not name:
            return False
        removed = False
        if sid is None:
            for key in list(self._bans):
                if key[0] == name:
                    self._bans.discard(key)
                    removed = True
        else:
            key = (name, sid)
            if key in self._bans:
                self._bans.discard(key)
                removed = True
        if removed:
            self._save()
        return removed

    def mark_offline(self, name: str, sid: str) -> None:
        agent = self._agents.get(name)
        if agent is None:
            return
        for h in agent.hosts:
            if h.sid == sid:
                h.status = "offline"
        self._save()

    def gc(self, max_age: float = SESSION_TIMEOUT) -> list[tuple[str, str]]:
        """Drop stale hosts; return [(agent_name, sid), ...] removed."""
        removed: list[tuple[str, str]] = []
        for agent in list(self._agents.values()):
            kept: list[HostSession] = []
            for h in agent.hosts:
                if h.effective_status(max_age) == "offline":
                    removed.append((agent.name, h.sid))
                else:
                    kept.append(h)
            agent.hosts = kept
            if not agent.hosts:
                del self._agents[agent.name]
                continue
            if agent.active_sid and not any(h.sid == agent.active_sid for h in agent.hosts):
                agent.active_sid = max(agent.hosts, key=lambda h: h.last_seen).sid
        if removed:
            self._save()
        return removed

    def probeable_active_hosts(self) -> list[tuple[str, HostSession]]:
        """(agent_name, active_host) pairs the server can push @-mentions to."""
        out: list[tuple[str, HostSession]] = []
        for agent in self._agents.values():
            host = agent.active_host
            if host and host.callback_url:
                out.append((agent.name, host))
        return out

    def probeable_hosts(self) -> list[tuple[str, HostSession]]:
        """(agent_name, host) for EVERY host with a callback 鈥?used by the
        liveness probe so all registered hosts stay alive, not just the active
        one. Kicked hosts are simply absent from this list (registry is the
        source of truth; heartbeat no-ops on a missing host)."""
        out: list[tuple[str, HostSession]] = []
        for agent in self._agents.values():
            for host in agent.hosts:
                if host.callback_url:
                    out.append((agent.name, host))
        return out
