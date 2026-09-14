"""Agent session registry (in-memory + on-disk)."""
from __future__ import annotations
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Session:
    name: str
    session_id: str
    callback_url: str | None = None
    last_seen: float = field(default_factory=time.time)
    status: str = "online"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Session":
        return cls(
            name=d["name"],
            session_id=d["session_id"],
            callback_url=d.get("callback_url"),
            last_seen=d.get("last_seen", time.time()),
            status=d.get("status", "online"),
        )


class SessionRegistry:
    def __init__(self, comms_dir: Path) -> None:
        self.comms_dir = Path(comms_dir)
        self.path = self.comms_dir / "_sessions.json"
        self._sessions: dict[str, Session] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for s in data.get("sessions", []):
            try:
                sess = Session.from_dict(s)
                self._sessions[sess.name] = sess
            except (KeyError, TypeError):
                continue

    def _save(self) -> None:
        data = {"sessions": [s.to_dict() for s in self._sessions.values()]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def handshake(self, name: str, callback_url: str | None = None) -> Session:
        now = time.time()
        if name in self._sessions:
            sess = self._sessions[name]
            sess.last_seen = now
            sess.status = "online"
            if callback_url is not None:
                sess.callback_url = callback_url
        else:
            sess = Session(
                name=name,
                session_id=uuid.uuid4().hex,
                callback_url=callback_url,
                last_seen=now,
                status="online",
            )
            self._sessions[name] = sess
        self._save()
        return sess

    def heartbeat(self, name: str) -> None:
        if name in self._sessions:
            self._sessions[name].last_seen = time.time()
            self._sessions[name].status = "online"
            self._save()

    def get(self, name: str) -> Session | None:
        return self._sessions.get(name)

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    def mark_offline(self, name: str) -> None:
        if name in self._sessions:
            self._sessions[name].status = "offline"
            self._save()
