"""WebSocket connection hub.

Tracks all connected WebSocket clients (browser GUIs) and broadcasts events to them.
Events:
- {kind: "message", message: {...full message dict...}}
- {kind: "session_joined", session: {...session dict...}}
- {kind: "session_removed", name: "..."}
- {kind: "snapshot", messages: [...], sessions: [...]}
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket


class ChatroomHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._sse_queues: set[asyncio.Queue[str]] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def sse_subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue()
        async with self._lock:
            self._sse_queues.add(q)
        return q

    async def sse_unsubscribe(self, q: asyncio.Queue[str]) -> None:
        async with self._lock:
            self._sse_queues.discard(q)

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Send `event` to all connected clients. Failures are silently dropped."""
        msg = json.dumps(event, ensure_ascii=False)
        if self._clients:
            async with self._lock:
                clients = list(self._clients)
            await asyncio.gather(
                *(self._safe_send(c, msg) for c in clients),
                return_exceptions=True,
            )
        if self._sse_queues:
            async with self._lock:
                queues = list(self._sse_queues)
            for q in queues:
                try:
                    q.put_nowait(msg)
                except Exception:
                    self._sse_queues.discard(q)

    async def _safe_send(self, ws: WebSocket, msg: str) -> None:
        try:
            await ws.send_text(msg)
        except Exception:
            # best-effort cleanup
            async with self._lock:
                self._clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)


# Global singleton; the server wires it into both /ws and the post handler.
hub = ChatroomHub()
