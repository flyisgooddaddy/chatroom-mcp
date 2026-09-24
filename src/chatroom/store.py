"""Append-only store backed by messages.jsonl.

P1 uses O_APPEND for atomic small writes; P2 adds cross-platform file lock.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from chatroom.protocol import is_well_formed

_ID_RE = re.compile(r"^msg-(\d+)$")


DEFAULT_ROOM = "main"


class Store:
    """Append-only per-room store backed by messages.jsonl / messages-<room>.jsonl."""

    def __init__(self, comms_dir: Path, room: str = DEFAULT_ROOM) -> None:
        self.comms_dir = Path(comms_dir)
        self.room = room or DEFAULT_ROOM
        if self.room == DEFAULT_ROOM:
            self.messages_path = self.comms_dir / "messages.jsonl"
        else:
            self.messages_path = self.comms_dir / f"messages-{self.room}.jsonl"
        self.comms_dir.mkdir(parents=True, exist_ok=True)
        if not self.messages_path.exists():
            self.messages_path.touch()

    def _scan_max_id(self) -> int:
        last = 0
        with self.messages_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = _ID_RE.match(str(obj.get("id", "")))
                if m:
                    last = max(last, int(m.group(1)))
        return last

    # -- persistent, monotonically-increasing message id -----------------------
    # Clear/rotation empties messages.jsonl but ids must keep growing, otherwise
    # any agent doing incremental pull via `after`/`since` goes deaf after Clear.
    # The counter lives in <comms_dir>/_id_seq.json and is ONLY reset when that
    # file is explicitly deleted (an intentional, user-initiated reset).

    def _id_seq_path(self) -> Path:
        return self.comms_dir / "_id_seq.json"

    def _read_seq(self) -> int:
        p = self._id_seq_path()
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return int(d.get("rooms", {}).get(self.room, 0))
        except (OSError, ValueError, TypeError):
            return 0

    def _write_seq(self, value: int) -> None:
        p = self._id_seq_path()
        d: dict[str, Any] = {}
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            d = {}
        if not isinstance(d, dict):
            d = {}
        rooms = d.get("rooms")
        if not isinstance(rooms, dict):
            rooms = {}
        rooms[self.room] = value
        d["rooms"] = rooms
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    def _next_id(self) -> int:
        seq = self._read_seq()
        if seq <= 0:
            seq = self._scan_max_id()  # migrate from an existing non-empty file
        return seq + 1

    def append(self, msg: dict[str, Any]) -> dict[str, Any]:
        if "id" not in msg:
            new_id = self._next_id()
            msg["id"] = f"msg-{new_id:04d}"
            self._write_seq(new_id)  # persist the monotonic counter
        if "timestamp" not in msg:
            msg["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        ok, reason = is_well_formed(msg)
        if not ok:
            raise ValueError(f"invalid message: {reason}")
        # O_APPEND is atomic for small writes on POSIX and Windows
        with self.messages_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        return msg

    def read_all(self) -> list[dict[str, Any]]:
        return list(self.iter_all())

    def last_id(self) -> str:
        """Highest msg-NNNN id present, or '' if none."""
        last = -1
        for m in self.iter_all():
            mm = _ID_RE.match(str(m.get("id", "")))
            if mm:
                last = max(last, int(mm.group(1)))
        return "" if last < 0 else f"msg-{last:04d}"

    def messages_after(self, since: str | None) -> list[dict[str, Any]]:
        cutoff = _ID_RE.match(since).group(1) if since and _ID_RE.match(since) else -1
        cutoff = int(cutoff)
        return [
            m
            for m in self.iter_all()
            if (mm := _ID_RE.match(str(m.get("id", "")))) and int(mm.group(1)) > cutoff
        ]

    def search(self, query: str, field: str | None = None) -> list[dict[str, Any]]:
        """Substring search (case-insensitive). field None => all text fields."""
        q = (query or "").strip().lower()
        if not q:
            return []
        out: list[dict[str, Any]] = []
        for m in self.iter_all():
            if field is not None:
                if q in str(m.get(field, "")).lower():
                    out.append(m)
                continue
            hay = " ".join(
                str(m.get(k, "")) for k in ("id", "from", "to", "type", "subject", "body")
            )
            if q in hay.lower():
                out.append(m)
        return out

    def iter_all(self) -> Iterator[dict[str, Any]]:
        with self.messages_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    # ---- 消息管理 (added by openwriter) ----

    def delete(self, msg_id: str) -> bool:
        """Delete a single message by id. Returns True if found & removed.

        Rewrites the jsonl atomically (tmp + replace). Backs up the
        previous file to <name>.bak-<ts> once per call.
        """
        if not msg_id:
            return False
        kept = [m for m in self.iter_all() if m.get("id") != msg_id]
        removed = len(kept) != sum(1 for _ in self.iter_all())
        if not removed:
            return False
        self._rewrite(kept)
        return True

    def clear(self) -> int:
        """Remove ALL messages from this room. Returns count removed."""
        n = sum(1 for _ in self.iter_all())
        self._rewrite([])
        return n

    def _rewrite(self, msgs: list[dict[str, Any]]) -> None:
        """Atomically replace the messages file with `msgs` (backs up once)."""
        import time as _time

        ts = _time.strftime("%Y%m%d-%H%M%S")
        if self.messages_path.exists() and self.messages_path.stat().st_size > 0:
            bak = self.messages_path.with_name(self.messages_path.name + f".bak-{ts}")
            bak.write_bytes(self.messages_path.read_bytes())
        tmp = self.messages_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for m in msgs:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        tmp.replace(self.messages_path)
