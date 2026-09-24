"""AgentClient: handshake + heartbeat + poll + claim + active marker + inject.

Direct port of the chatroom-bridge OpenCode plugin (TypeScript) to Python.
The TS plugin had three pieces worth lifting:

  1. Exactly-once delivery across N concurrent agent processes / restarts
     via atomic file create (O_CREAT|O_EXCL). Implemented as ChatroomClaim.
  2. Active-session marker that records which session the human is currently
     driving, so we know which opencode/agent session to inject into.
     Implemented as AgentState.
  3. Long-poll loop that pulls messages, dedupes via (1), filters via
     is_targeting(), and dispatches to a framework-specific injector.
     Implemented as AgentClient.
"""
from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .config import AgentConfig

log = logging.getLogger("chatroom_client")

MCP_PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "chatroom-client", "version": "0.1.0"}


# ----- exactly-once claims ----------------------------------------------------

class ChatroomClaim:
    """Atomic exclusive-create dedup, port of claimOnce() in bridge.ts.

    Why not an in-memory Set: each of N agent processes has its own
    memory and would all think they're first. O_CREAT|O_EXCL is atomic at
    the OS level, so exactly one caller wins across processes and restarts.
    """

    def __init__(self, claims_dir: Path, *, retention_days: int = 7) -> None:
        self._dir = claims_dir
        self._retention_days = retention_days

    def ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def try_acquire(self, msg_id: str) -> bool:
        """Return True iff this caller wins the right to process msg_id.

        Never blocks delivery on filesystem errors: returns True (claim
        succeeds) so a broken lock cannot stop agents from replying.
        """
        path = self._dir / msg_id
        try:
            self.ensure_dir()
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(fd)
            return True
        except OSError as e:
            if e.errno == errno.EEXIST:
                return False
            log.warning("claimOnce %s failed unexpectedly: %s", msg_id, e)
            return True  # never block on lock errors

    def prune(self) -> None:
        """Drop claim files older than retention_days. Call at startup."""
        try:
            cutoff = time.time() - self._retention_days * 86400
            for entry in os.listdir(self._dir):
                p = self._dir / entry
                try:
                    if p.stat().st_mtime < cutoff:
                        p.unlink(missing_ok=True)
                except OSError:
                    continue
        except FileNotFoundError:
            return
        except OSError as e:
            log.warning("prune claims failed: %s", e)


# ----- active-session marker & injection-echo guard --------------------------

class AgentState:
    """Active-session marker + last-injection record.

    Multiple agent processes may run concurrently. They each observe the
    server's event bus (or, in CLI mode, an external signal) and write
    the most recent user-driven session id to ACTIVE_FILE. The poll loop
    then injects targeting messages into whichever session id the human
    is currently driving.

    LASTINJECT_FILE records the last session we injected into, used to
    suppress the next role=user event as our own echo (otherwise the
    active marker would get pinned to the injected session forever).
    """

    ECHO_WINDOW_S = 20.0

    def __init__(self, marker_path: Path, lastinject_path: Path) -> None:
        self._marker = marker_path
        self._lastinject = lastinject_path
        self._marker.parent.mkdir(parents=True, exist_ok=True)

    def record_active(self, sid: str) -> None:
        if not sid:
            return
        self._atomic_write(self._marker, f"{int(time.time() * 1000)} {sid}\n")

    def read_active(self) -> str | None:
        try:
            raw = self._marker.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        parts = raw.split()
        sid = parts[1] if len(parts) >= 2 else ""
        return sid if sid.startswith("ses_") else None

    def note_injection(self, sid: str) -> None:
        self._atomic_write(self._lastinject, f"{int(time.time() * 1000)} {sid}\n")

    def is_own_injection_echo(self, sid: str) -> bool:
        try:
            raw = self._lastinject.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return False
        parts = raw.split()
        if len(parts) < 2 or not parts[1].startswith("ses_"):
            return False
        try:
            ts_ms = int(parts[0])
        except ValueError:
            return False
        return parts[1] == sid and (time.time() * 1000 - ts_ms) < self.ECHO_WINDOW_S * 1000

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """Write via tmp + os.replace to avoid torn reads."""
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)


# ----- transport: MCP JSON-RPC over StreamableHTTP ----------------------------

class McpError(RuntimeError):
    """Raised when the MCP server returns a JSON-RPC error or non-2xx."""


@dataclass(slots=True)
class JsonRpcResult:
    raw: dict[str, Any]


class ChatroomTransport:
    """MCP JSON-RPC client over StreamableHTTP, port of mcpInitialize/mcpCallTool."""

    def __init__(
        self,
        server_url: str,
        *,
        timeout_s: float = 8.0,
        poll_timeout_s: float = 25.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = server_url.rstrip("/")
        self._mcp_url = f"{self._base}/mcp"
        self._timeout_s = timeout_s
        self._poll_timeout_s = poll_timeout_s
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._owns_client = client is None
        self._session_id: str | None = None
        self._init_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _ensure_initialized(self) -> None:
        async with self._init_lock:
            if self._session_id:
                return
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                },
            }
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            r = await self._client.post(self._mcp_url, json=payload, headers=headers)
            r.raise_for_status()
            sid = r.headers.get("mcp-session-id")
            if sid:
                self._session_id = sid
            await self._send_initialized_notification()

    async def _send_initialized_notification(self) -> None:
        payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        await self._post(payload, ignore_session_loss=True)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        for attempt in (1, 2):
            await self._ensure_initialized()
            payload = {
                "jsonrpc": "2.0",
                "id": int(time.time() * 1000),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
            try:
                return await self._post(payload)
            except McpError as e:
                if attempt == 1 and "session" in str(e).lower():
                    self._session_id = None
                    continue
                raise

    async def _post(self, payload: dict[str, Any], *, ignore_session_loss: bool = False) -> Any:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        r = await self._client.post(self._mcp_url, json=payload, headers=headers)
        if r.status_code == 202 and ignore_session_loss:
            return None
        if not r.is_success:
            raise McpError(f"http {r.status_code}: {r.text[:200]}")
        return self._parse_response(r)

    @staticmethod
    def _parse_response(r: httpx.Response) -> Any:
        ct = r.headers.get("content-type", "")
        body = r.text
        if ct.startswith("application/json"):
            return _unwrap(body)
        # SSE: parse the first data: line
        for line in body.splitlines():
            if line.startswith("data: "):
                return _unwrap(line[6:])
        raise McpError(f"mcp response missing data line: {body[:200]}")


def _unwrap(body: str) -> Any:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise McpError(f"invalid json: {e}") from e
    if isinstance(payload, dict) and "error" in payload:
        err = payload["error"]
        raise McpError(f"jsonrpc error: {err}")
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


# ----- targeting filter -------------------------------------------------------

def is_targeting(msg: dict[str, Any], names: list[str]) -> bool:
    """Return True iff `msg` is targeting any of the agent's names.

    Mirrors isTargeting() in chatroom-bridge.ts:
      - skip self-sent messages (echo of our own @-reply)
      - match on `to` field (exact or contains)
      - match on @name in subject or body
    """
    if not names:
        return False
    sender = str(msg.get("from", ""))
    if sender in names:
        return False
    to = str(msg.get("to", ""))
    if any(n == to or n in to for n in names):
        return True
    subj = str(msg.get("subject", ""))
    if any(f"@{n}" in subj for n in names):
        return True
    body = str(msg.get("body", ""))
    if any(f"@{n}" in body for n in names):
        return True
    return False


# ----- main agent client ------------------------------------------------------

Injector = Callable[[dict[str, Any], str], Awaitable[None]]


class AgentClient:
    """Handshake + heartbeat + long-poll + claim + inject loop."""

    def __init__(
        self,
        transport: ChatroomTransport,
        config: AgentConfig,
        *,
        injector: Injector | None = None,
        state: AgentState | None = None,
        claim: ChatroomClaim | None = None,
    ) -> None:
        self._t = transport
        self._cfg = config
        self._injector = injector or self._default_injector
        self._state = state or AgentState(config.session_marker_path, config.lastinject_path)
        self._claim = claim or ChatroomClaim(config.claims_dir)
        self._stopped = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._last_id: str = ""
        self._primed = False

    async def handshake(self, *, session_id: str = "") -> dict[str, Any]:
        """Register (or refresh) this agent with the chatroom server."""
        result = await self._t.call_tool(
            "chatroom_handshake",
            {
                "name": self._cfg.agent.name,
                "bound_session_id": session_id or f"{self._cfg.agent.name}-sdk-001",
                "callback_url": "",
                "session_name": self._cfg.agent.name,
            },
        )
        # tools/call returns {"content": [...], "structuredContent": {...}}
        structured = result.get("structuredContent") if isinstance(result, dict) else None
        return structured or result

    async def _heartbeat_loop(self) -> None:
        interval = self._cfg.heartbeat_seconds
        while not self._stopped.is_set():
            try:
                await self.handshake()
                log.debug("heartbeat ok")
            except Exception as e:
                log.warning("heartbeat failed: %s", e)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
            except TimeoutError:
                continue

    async def start(self) -> None:
        self._claim.prune()
        await self.handshake()
        self._tasks.append(asyncio.create_task(self._heartbeat_loop(), name="chatroom-heartbeat"))
        self._tasks.append(asyncio.create_task(self._poll_loop(), name="chatroom-poll"))

    async def stop(self) -> None:
        self._stopped.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        await self._t.aclose()

    async def run_forever(self) -> None:
        await self.start()
        try:
            await self._stopped.wait()
        finally:
            await self.stop()

    async def _poll_loop(self) -> None:
        poll_url = f"{self._cfg.server.url}/api/messages/poll"
        timeout_s = self._cfg.poll_timeout_seconds
        params = {"room": self._cfg.server.room, "after": "", "timeout": str(int(timeout_s))}
        log.info("poll_loop started url=%s", poll_url)
        while not self._stopped.is_set():
            try:
                params["after"] = self._last_id or ""
                log.debug("poll GET %s after=%s", poll_url, params["after"])
                r = await self._t._client.get(
                    poll_url,
                    params=params,
                    headers={"Accept": "application/json"},
                    timeout=timeout_s + 5,
                )
                r.raise_for_status()
                data = r.json()
                fresh = data.get("messages") or []
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("poll loop: %s", e)
                await asyncio.sleep(2)
                continue

            if not fresh:
                continue
            if not self._primed:
                self._primed = True
                self._last_id = fresh[-1].get("id", self._last_id)
                log.info("primed at %s (skipped %d historical)", self._last_id, len(fresh))
                continue
            for msg in fresh:
                self._last_id = msg.get("id", self._last_id)
                if not is_targeting(msg, self._cfg.all_names):
                    continue
                marker = self._state.read_active()
                if not marker:
                    log.info("skip %s: no active marker", msg.get("id"))
                    continue
                if not self._claim.try_acquire(msg["id"]):
                    log.info("dedup-skip %s", msg.get("id"))
                    continue
                log.info("HIT %s from=%s target=%s", msg.get("id"), msg.get("from"), marker)
                try:
                    await self._injector(msg, marker)
                    self._state.note_injection(marker)
                except Exception as e:
                    log.warning("inject failed for %s: %s", msg.get("id"), e)

    async def _default_injector(self, msg: dict[str, Any], target: str) -> None:
        """When no framework driver is wired up, log the would-be injection."""
        log.info(
            "would-inject id=%s target=%s subj=%r",
            msg.get("id"),
            target,
            msg.get("subject", ""),
        )

    # helpers exposed for drivers
    def record_active(self, sid: str) -> None:
        self._state.record_active(sid)

    def is_own_injection_echo(self, sid: str) -> bool:
        return self._state.is_own_injection_echo(sid)

    @property
    def config(self) -> AgentConfig:
        return self._cfg

    @property
    def transport(self) -> ChatroomTransport:
        return self._t


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
