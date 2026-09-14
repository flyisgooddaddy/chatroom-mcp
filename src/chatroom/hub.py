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
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Send `event` to all connected clients. Failures are silently dropped."""
        if not self._clients:
            return
        msg = json.dumps(event, ensure_ascii=False)
        async with self._lock:
            clients = list(self._clients)
        # send concurrently
        results = await asyncio.gather(
            *(self._safe_send(c, msg) for c in clients),
            return_exceptions=True,
        )

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
