"""Append-only store backed by messages.jsonl.

P1 uses O_APPEND for atomic small writes; P2 adds cross-platform file lock.
"""
from __future__ import annotations
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterator

from chatroom.protocol import is_well_formed

_ID_RE = re.compile(r"^msg-(\d+)$")


class Store:
    def __init__(self, comms_dir: Path) -> None:
        self.comms_dir = Path(comms_dir)
        self.messages_path = self.comms_dir / "messages.jsonl"
        self.comms_dir.mkdir(parents=True, exist_ok=True)
        if not self.messages_path.exists():
            self.messages_path.touch()

    def _next_id(self) -> int:
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
        return last + 1

    def append(self, msg: dict[str, Any]) -> dict[str, Any]:
        if "id" not in msg:
            msg["id"] = f"msg-{self._next_id():04d}"
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
