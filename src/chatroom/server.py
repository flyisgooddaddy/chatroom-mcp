"""MCP server + Web GUI (FastMCP 1.x + FastAPI routes).

Serves:
  POST /mcp                  MCP streamable-HTTP (FastMCP) — 7 tools + 1 resource
  GET  /                     Web GUI (index.html)
  GET  /static/*             JS / CSS / favicon
  GET  /api/rooms            List available rooms
  GET  /api/messages         List messages (room, limit, after, before)
  DELETE /api/messages/{id}  Delete one message
  DELETE /api/messages       Clear room
  GET  /api/messages/poll    Long-poll for new messages
  GET  /api/search           Full-text search over messages
  GET  /api/stream           Server-Sent Events (real-time push)
  GET  /api/sessions         List registered agent sessions
  POST /api/post             Post a message from the GUI
  DELETE /api/sessions/{name}    Kick + ban an agent (persistent)
  DELETE /api/agents/{name}/hosts/{sid}  Kick + ban a single host (persistent)
  GET  /api/bans                 List all (name, sid) entries in the ban list
  WS   /ws                   WebSocket broadcast + snapshot
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from chatroom.hub import hub
from chatroom.sessions import Agent, AgentRegistry, HostSession
from chatroom.store import DEFAULT_ROOM, Store

TOOL_HANDSHAKE = "chatroom_handshake"
TOOL_PULL = "chatroom_pull"
TOOL_POST = "chatroom_post"
TOOL_HISTORY = "chatroom_history"
TOOL_SESSIONS = "chatroom_sessions"
TOOL_SEARCH = "chatroom_search"
TOOL_ROOMS = "chatroom_rooms"
RESOURCE_MESSAGES = "chat://messages"

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_DIR = WEB_DIR / "static"

SSE_HEARTBEAT = 15.0


async def _probe_one(sessions, name: str, sid: str, callback_url: str) -> bool:
    """GET callback_url; on 2xx, refresh last_seen. Returns success.

    Module-level so the contract is independently testable; the lifespan's
    background loop just iterates and calls this.
    """
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            r = await client.get(callback_url)
        if 200 <= r.status_code < 300:
            sessions.heartbeat(name, sid)
            return True
    except Exception:
        pass
    return False


def _msg_id_key(msg_id: str) -> int:
    try:
        return int(msg_id.split("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _resolve_push_target(target: str, sessions):
    """Look up the (callback_url, agent_name, host) tuple to push @-mention to.

    Two-stage lookup:
      1. `target` is an agent name → use its active host (live; falls back to
         most-recent live if active_sid unset).
      2. `target` is a session_name under some agent → push to that specific host.

    Agent-name match wins over session-name match on collision.
    Returns (callback_url, agent_name, host_sid) or None.
    """
    if not target:
        return None
    try:
        sessions._load()
    except Exception:
        pass
    agent = sessions.get_agent(target)
    if agent is not None:
        host = agent.active_host
        if host and host.callback_url:
            return host.callback_url, agent.name, host.sid
        return None
    # Fallback: search all agents' hosts for a matching session_name
    matches: list[tuple[Agent, HostSession]] = []
    for a in sessions.all_agents():
        for h in a.hosts:
            if h.session_name == target and h.callback_url:
                matches.append((a, h))
    if len(matches) == 1:
        a, h = matches[0]
        return h.callback_url, a.name, h.sid
    if len(matches) > 1:
        # Ambiguous: prefer the one that is currently active in its parent agent.
        for a, h in matches:
            if a.active_sid == h.sid:
                return h.callback_url, a.name, h.sid
        # Still ambiguous — go with the first but caller may want to log.
        a, h = matches[0]
        return h.callback_url, a.name, h.sid
    return None


async def _notify_and_broadcast(stored: dict[str, Any], sessions) -> None:
    """Fire push to @-target (agent name or session_name) + broadcast.

    Push is routed to the resolved host's callback_url. If `target` doesn't
    resolve (no agent, no host with that session_name, host has no callback,
    or all hosts stale), the push is silently dropped.
    """
    target = stored.get("to")
    if target:
        resolved = _resolve_push_target(target, sessions)
        if resolved:
            callback_url, _agent_name, _sid = resolved
            try:
                from chatroom.push import notify

                await notify(callback_url, {"event": "chatroom_message", "message": stored})
            except Exception:
                pass
    await hub.broadcast({"kind": "message", "message": stored})


def _store_for(stores: dict[str, Store], room: str | None) -> Store:
    return stores.get(room or DEFAULT_ROOM, stores[DEFAULT_ROOM])


def _build_mcp(stores: dict[str, Store], sessions=None) -> FastMCP:
    """Build the FastMCP instance + register tools. Reads/writes via store registry."""
    if sessions is None:
        # sessions not stored by room; first room's registry is the canonical one
        sessions = AgentRegistry(next(iter(stores.values())).comms_dir)

    mcp = FastMCP(
        name="chatroom-mcp",
        # Serve the streamable-HTTP endpoint at the app root so that mounting this
        # app under /mcp yields the documented endpoint at /mcp/ (not /mcp/mcp).
        streamable_http_path="/",
        # LAN / cross-machine: FastMCP auto-enables DNS-rebinding protection when
        # host is 127.0.0.1/localhost, which whitelists only those hosts and makes
        # the MCP endpoint return 421 "Invalid Host header" for any LAN IP.
        # We run on 0.0.0.0 for LAN access, so disable the host allowlist here.
        # (Safer alternative: keep it on and whitelist your LAN IP, e.g.
        #  allowed_hosts=["127.0.0.1:*","localhost:*","[::1]:*","<YOUR_LAN_IP>:*"].)
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        instructions=(
            "A shared chatroom for AI agents and humans. "
            "Use chatroom_handshake to register, chatroom_pull to fetch, "
            "chatroom_post to send, chatroom_search to search. "
            "Web GUI runs at GET /."
        ),
    )

    @mcp.tool(
        name=TOOL_HANDSHAKE,
        description=(
            "Register or refresh an agent host. callback_url is optional; provide it "
            "only if you expose an HTTP endpoint the chatroom can POST @-mentions to. "
            "Without it, @-mentions are still stored and retrievable via chatroom_pull, "
            "but no push is attempted. Multiple hosts per agent name are allowed; the "
            "latest handshake becomes the active binding."
        ),
    )
    async def handshake(
        name: str,
        bound_session_id: str,
        callback_url: str = "",
        session_name: str | None = None,
    ) -> dict[str, Any]:
        try:
            agent = sessions.handshake(
                name=name,
                bound_session_id=bound_session_id,
                callback_url=callback_url,
                session_name=session_name,
            )
        except ValueError as e:
            return {"error": {"code": -32602, "message": str(e)}}
        await hub.broadcast({"kind": "agent_joined", "agent": agent.to_dict()})
        return agent.to_dict()

    @mcp.tool(
        name=TOOL_PULL,
        description="Pull messages newer than `since`. Empty/null for all. Room-aware.",
    )
    def pull(since: str | None = None, limit: int = 50, room: str = DEFAULT_ROOM) -> dict[str, Any]:
        store = _store_for(stores, room)
        if since:
            out = store.messages_after(since)
        else:
            out = list(store.iter_all())
        return {"messages": out[-max(1, min(limit, 1000)) :]}

    @mcp.tool(
        name=TOOL_POST,
        description="Post a new message to a room. Pushes to @-target agent if registered.",
    )
    async def post(msg: dict[str, Any], room: str = DEFAULT_ROOM) -> dict[str, Any]:
        store = _store_for(stores, room)
        msg.setdefault("room", _store_for(stores, room).room)
        try:
            stored = store.append(msg)
        except ValueError as e:
            return {"error": {"code": -32602, "message": str(e)}}
        await _notify_and_broadcast(stored, sessions)
        return stored

    @mcp.tool(
        name=TOOL_HISTORY,
        description="Return the most recent N messages (default 50, max 1000). Room-aware.",
    )
    def history(limit: int = 50, room: str = DEFAULT_ROOM) -> dict[str, Any]:
        limit = max(1, min(limit, 1000))
        store = _store_for(stores, room)
        return {"messages": store.read_all()[-limit:]}

    @mcp.tool(name=TOOL_SESSIONS, description="List known agents (each with its hosts).")
    def list_sessions() -> dict[str, Any]:
        return {"agents": [a.to_dict() for a in sessions.all_agents()]}

    @mcp.tool(
        name=TOOL_SEARCH,
        description="Full-text search over messages. field in {subject,body,from,type,id}.",
    )
    def search(query: str, field: str | None = None, room: str = DEFAULT_ROOM) -> dict[str, Any]:
        store = _store_for(stores, room)
        return {"messages": store.search(query, field)}

    @mcp.tool(name=TOOL_ROOMS, description="List available rooms.")
    def rooms() -> dict[str, Any]:
        return {"rooms": list(stores.keys())}

    @mcp.resource(
        uri=RESOURCE_MESSAGES,
        name="chatroom messages",
        description="All messages as NDJSON.",
        mime_type="application/x-ndjson",
    )
    def messages_resource() -> str:
        lines = []
        for store in stores.values():
            lines.extend(json.dumps(m, ensure_ascii=False) for m in store.iter_all())
        return "\n".join(lines)

    return mcp


def create_app(comms_dir: Path, rooms: list[str] | None = None) -> FastAPI:
    """Build the full ASGI app (MCP + Web GUI + WS + SSE). Rooms may be a list;
    default single-room 'main' keeps backward compatibility."""
    comms_dir = Path(comms_dir)
    room_names = list(dict.fromkeys([r for r in (rooms or [DEFAULT_ROOM]) if r]))
    if DEFAULT_ROOM not in room_names:
        room_names.insert(0, DEFAULT_ROOM)

    stores: dict[str, Store] = {r: Store(comms_dir, r) for r in room_names}
    sessions = AgentRegistry(comms_dir)
    mcp = _build_mcp(stores, sessions=sessions)
    # Build the MCP ASGI app up front so mcp.session_manager is initialized.
    # NOTE: Starlette's Mount does NOT run the child app's lifespan, so the
    # StreamableHTTPSessionManager task group must be driven from the parent
    # app's lifespan below -- otherwise every MCP request 500s with
    # "Task group is not initialized. Make sure to use run()."
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            probe_task = asyncio.create_task(_probe_loop())
            try:
                yield
            finally:
                probe_task.cancel()

    probe_interval = 30.0

    async def _probe_loop() -> None:
        # Two rails: (a) probe active host callback_urls, (b) TTL GC stale hosts.
        # This is the chatroom's self-cleaning contract: registration is not
        # enough, liveness must be observable from chatroom's side.
        while True:
            try:
                for name, host in sessions.probeable_hosts():
                    await _probe_one(sessions, name, host.sid, host.callback_url)
                stale = sessions.gc()
                for agent_name, sid in stale:
                    await hub.broadcast({"kind": "host_removed", "agent": agent_name, "sid": sid})
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(probe_interval)

    app = FastAPI(title="chatroom-mcp", version="0.2.0", lifespan=lifespan)
    app.state.sessions = sessions
    app.state.stores = stores
    app.state.rooms = room_names
    app.state.mcp = mcp

    @app.get("/", include_in_schema=False)
    async def index():
        idx = WEB_DIR / "index.html"
        if not idx.exists():
            return JSONResponse({"error": "GUI not built; see src/chatroom/web/"}, status_code=500)
        return FileResponse(idx)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # --- REST API for the GUI ---
    @app.get("/api/rooms")
    async def api_rooms() -> dict[str, Any]:
        return {"rooms": room_names}

    @app.get("/api/messages")
    async def api_messages(
        room: str = DEFAULT_ROOM,
        limit: int = 200,
        after: str | None = None,
        before: str | None = None,
    ) -> dict[str, Any]:
        store = _store_for(stores, room)
        msgs = store.messages_after(after) if after else store.read_all()
        if before:
            # pagination: return messages with id < before (older ones)
            msgs = [m for m in msgs if str(m.get("id", "")) < str(before)]
        limit = max(1, min(limit, 1000))
        # When paginating backwards, return the LAST `limit` of the filtered set
        return {"messages": msgs[-limit:], "total": len(store.read_all())}

    @app.delete("/api/messages/{msg_id}")
    async def api_delete_message(msg_id: str, room: str = DEFAULT_ROOM) -> dict[str, Any]:
        """Delete one message by id."""
        store = _store_for(stores, room)
        ok = store.delete(msg_id)
        if not ok:
            raise HTTPException(404, f"no message {msg_id!r}")
        await hub.broadcast({"kind": "message_deleted", "id": msg_id, "room": store.room})
        return {"deleted": msg_id}

    @app.delete("/api/messages")
    async def api_clear_messages(room: str = DEFAULT_ROOM) -> dict[str, Any]:
        """Delete ALL messages in a room (backs up the file first)."""
        store = _store_for(stores, room)
        n = store.clear()
        await hub.broadcast({"kind": "cleared", "room": store.room, "count": n})
        return {"cleared": n, "room": store.room}

    @app.get("/api/messages/poll")
    async def api_long_poll(
        room: str = DEFAULT_ROOM, after: str | None = None, timeout: float = 15.0
    ) -> dict[str, Any]:
        """Long-poll: return immediately if new messages exist, else wait up to `timeout`."""
        store = _store_for(stores, room)
        now = store.messages_after(after)
        if now:
            return {"messages": now}
        q = await hub.sse_subscribe()
        try:
            deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(q.get(), timeout=remaining)
                except TimeoutError:
                    break
                evt = json.loads(raw)
                evt_room = evt.get("kind") == "message" and evt["message"].get("room", DEFAULT_ROOM)
                if evt["kind"] == "message" and evt_room == store.room:
                    break
        finally:
            await hub.sse_unsubscribe(q)
        return {"messages": store.messages_after(after)}

    @app.get("/api/search")
    async def api_search(
        q: str = Query(..., min_length=1), field: str | None = None, room: str = DEFAULT_ROOM
    ) -> dict[str, Any]:
        store = _store_for(stores, room)
        return {"messages": store.search(q, field)}

    @app.get("/api/stream")
    async def api_stream(room: str = DEFAULT_ROOM):
        """Server-Sent Events: real-time push of message + session events."""
        store = _store_for(stores, room)
        q = await hub.sse_subscribe()

        async def gen():
            try:
                while True:
                    try:
                        raw = await asyncio.wait_for(q.get(), timeout=SSE_HEARTBEAT)
                        evt = json.loads(raw)
                        if evt.get("kind") == "message":
                            evt_room = evt["message"].get("room", DEFAULT_ROOM)
                            if evt_room != store.room:
                                continue
                        yield f"data: {raw}\n\n"
                    except TimeoutError:
                        yield f": heartbeat {int(time.time())}\n\n"
            except asyncio.CancelledError:
                raise
            finally:
                await hub.sse_unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/sessions")
    async def api_sessions() -> dict[str, Any]:
        # Lazy GC so a long-idle sidebar can never show zombie hosts.
        sessions.gc()
        return {"agents": [a.to_dict() for a in sessions.all_agents()]}

    @app.post("/api/post")
    async def api_post(payload: dict[str, Any], room: str = DEFAULT_ROOM) -> dict[str, Any]:
        """GUI posts a message as 'human'. Auto-fills id/timestamp."""
        msg = payload.get("msg")
        if not isinstance(msg, dict):
            raise HTTPException(400, "msg must be a dict")
        msg.setdefault("from", "human")
        store = _store_for(stores, room)
        msg.setdefault("room", store.room)
        try:
            stored = store.append(msg)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        await _notify_and_broadcast(stored, sessions)
        return stored

    @app.delete("/api/sessions/{name}")
    async def api_kick(name: str) -> dict[str, Any]:
        """Ban + remove an agent (and all its hosts) from the registry + sidebar.

        Kick is now persistent: the (name) ban is added to the persistent ban
        list, so even if the adapter keeps calling chatroom_handshake every 30s
        the call is rejected and the agent stays disconnected until the ban is
        removed by editing the "bans" array in _sessions.json.
        """
        sessions._load()
        if not sessions.get_agent(name) and not sessions.is_banned(name):
            raise HTTPException(404, f"no agent named {name!r}")
        sessions.ban(name)
        await hub.broadcast({"kind": "agent_removed", "name": name})
        return {"removed": name, "banned": True}

    @app.post("/api/agents/{name}/active")
    async def api_set_active(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Switch the active host of an agent (which sid gets @-push)."""
        sid = payload.get("sid")
        if not sid:
            raise HTTPException(400, "sid required")
        try:
            agent = sessions.set_active(name, sid)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        await hub.broadcast({"kind": "active_changed", "agent": agent.to_dict()})
        return agent.to_dict()

    @app.delete("/api/agents/{name}/hosts/{sid}")
    async def api_remove_host(name: str, sid: str) -> dict[str, Any]:
        """Ban + remove a single host (sid). Other sids under the same agent
        can still register; only this (name, sid) is rejected on handshake.
        """
        if not sessions.get_agent(name) and not sessions.is_banned(name, sid):
            raise HTTPException(404, f"host {sid!r} of agent {name!r} not found")
        sessions.ban(name, sid)
        await hub.broadcast({"kind": "host_removed", "agent": name, "sid": sid})
        return {"removed": {"agent": name, "sid": sid}, "banned": True}

    @app.get("/api/bans")
    async def api_list_bans() -> dict[str, Any]:
        """List all (name, sid) entries currently in the persistent ban list.
        sid=null means the whole agent name is banned (any sid rejected)."""
        return {"bans": [{"name": n, "sid": s} for (n, s) in sessions.banned()]}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await hub.connect(websocket)
        try:
            snapshot = {
                "kind": "snapshot",
                "messages": stores[DEFAULT_ROOM].read_all()[-100:],
                "agents": [a.to_dict() for a in sessions.all_agents()],
                "rooms": room_names,
            }
            try:
                await websocket.send_text(json.dumps(snapshot, ensure_ascii=False))
            except Exception:
                pass
            while True:
                msg = await websocket.receive_text()
                if msg == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(websocket)

    # Mount MCP streamable HTTP at /mcp (endpoint serves at /mcp/)
    app.mount("/mcp", mcp_app)

    @app.middleware("http")
    async def _mcp_alias_no_trailing_slash(request, call_next):
        """Serve /mcp (no trailing slash) as /mcp/ instead of 307-redirecting.

        Starlette's Mount redirects `/mcp` -> `/mcp/` with 307, but streamable-HTTP
        clients POST `initialize` to `/mcp` and many of them do not follow redirects
        (a 307 on POST requires replaying the body), so the connection never completes.
        Rewrite the path in-process so both spellings work.
        """
        if request.scope.get("path", "").rstrip("/") == "/mcp":
            request.scope["path"] = "/mcp/"
            request.scope["raw_path"] = b"/mcp/"
        return await call_next(request)

    return app


def make_app(comms_dir: Path, rooms: list[str] | None = None):
    return create_app(comms_dir, rooms)


# Back-compat alias for tests/scripts that imported create_server.
create_server = create_app


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Run chatroom-mcp server + Web GUI.")
    p.add_argument("--comms-dir", type=Path, required=True)
    p.add_argument(
        "--rooms",
        nargs="*",
        default=None,
        help="room names (default: main). Multi-room via e.g. --rooms main ops research",
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7777)
    a = p.parse_args()
    import uvicorn

    uvicorn.run(create_app(a.comms_dir, a.rooms), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
