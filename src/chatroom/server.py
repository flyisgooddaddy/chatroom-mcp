"""MCP server + Web GUI (FastMCP 1.x + FastAPI routes).

Serves:
  POST /mcp/get_stream /mcp  (MCP streamable-HTTP via FastMCP)
  GET  /                     Web GUI (index.html)
  GET  /static/*             JS / CSS / favicon
  GET  /api/messages         JSON list of messages (room-aware)
  GET  /api/messages/poll    Long-poll: block until a new message or timeout
  GET  /api/search           Full-text search over messages (room-aware)
  GET  /api/stream           Server-Sent Events (real-time push)
  GET  /api/rooms            List available rooms
  GET  /api/sessions         JSON list of sessions
  POST /api/post             Post a message from the GUI (room-aware)
  DELETE /api/sessions/{name}  Kick a session
  WS   /ws                   WebSocket: broadcasts new messages + session events
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP

from chatroom.hub import hub
from chatroom.sessions import SessionRegistry
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


def _msg_id_key(msg_id: str) -> int:
    try:
        return int(msg_id.split("-", 1)[1])
    except (IndexError, ValueError):
        return 0


async def _notify_and_broadcast(stored: dict[str, Any], sessions) -> None:
    """Fire push to @-target agent (best-effort) + broadcast to WS/SSE clients."""
    target = stored.get("to")
    if target:
        try:
            sessions._load()
        except Exception:
            pass
        sess = sessions.get(target)
        if sess and sess.callback_url:
            try:
                from chatroom.push import notify
                await notify(sess.callback_url, {"event": "chatroom_message", "message": stored})
            except Exception:
                pass
    await hub.broadcast({"kind": "message", "message": stored})


def _store_for(stores: dict[str, Store], room: str | None) -> Store:
    return stores.get(room or DEFAULT_ROOM, stores[DEFAULT_ROOM])


def _build_mcp(stores: dict[str, Store], sessions=None) -> FastMCP:
    """Build the FastMCP instance + register tools. Reads/writes via store registry."""
    if sessions is None:
        # sessions not stored by room; first room's registry is the canonical one
        sessions = SessionRegistry(next(iter(stores.values())).comms_dir)

    mcp = FastMCP(
        name="chatroom-mcp",
        # Serve the streamable-HTTP endpoint at the app root so that mounting this
        # app under /mcp yields the documented endpoint at /mcp/ (not /mcp/mcp).
        streamable_http_path="/",
        instructions=(
            "A shared chatroom for AI agents and humans. "
            "Use chatroom_handshake to register, chatroom_pull to fetch, "
            "chatroom_post to send, chatroom_search to search. "
            "Web GUI runs at GET /."
        ),
    )

    @mcp.tool(name=TOOL_HANDSHAKE, description="Register or refresh an agent session.")
    async def handshake(name: str, callback_url: str | None = None) -> dict[str, Any]:
        sess = sessions.handshake(name=name, callback_url=callback_url)
        await hub.broadcast({"kind": "session_joined", "session": sess.to_dict()})
        return sess.to_dict()

    @mcp.tool(name=TOOL_PULL, description="Pull messages newer than `since`. Empty/null for all. Room-aware.")
    def pull(since: str | None = None, limit: int = 50, room: str = DEFAULT_ROOM) -> dict[str, Any]:
        store = _store_for(stores, room)
        if since:
            out = store.messages_after(since)
        else:
            out = list(store.iter_all())
        return {"messages": out[-max(1, min(limit, 1000)):]}

    @mcp.tool(name=TOOL_POST, description="Post a new message to a room. Pushes to @-target agent if registered.")
    async def post(msg: dict[str, Any], room: str = DEFAULT_ROOM) -> dict[str, Any]:
        store = _store_for(stores, room)
        msg.setdefault("room", _store_for(stores, room).room)
        try:
            stored = store.append(msg)
        except ValueError as e:
            return {"error": {"code": -32602, "message": str(e)}}
        await _notify_and_broadcast(stored, sessions)
        return stored

    @mcp.tool(name=TOOL_HISTORY, description="Return the most recent N messages (default 50, max 1000). Room-aware.")
    def history(limit: int = 50, room: str = DEFAULT_ROOM) -> dict[str, Any]:
        limit = max(1, min(limit, 1000))
        store = _store_for(stores, room)
        return {"messages": store.read_all()[-limit:]}

    @mcp.tool(name=TOOL_SESSIONS, description="List known agent sessions.")
    def list_sessions() -> dict[str, Any]:
        return {"sessions": [s.to_dict() for s in sessions.all()]}

    @mcp.tool(name=TOOL_SEARCH, description="Full-text search over messages. field in {subject,body,from,type,id}.")
    def search(query: str, field: str | None = None, room: str = DEFAULT_ROOM) -> dict[str, Any]:
        store = _store_for(stores, room)
        return {"messages": store.search(query, field)}

    @mcp.tool(name=TOOL_ROOMS, description="List available rooms.")
    def rooms() -> dict[str, Any]:
        return {"rooms": list(stores.keys())}

    @mcp.resource(uri=RESOURCE_MESSAGES, name="chatroom messages",
                  description="All messages as NDJSON.",
                  mime_type="application/x-ndjson")
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
    sessions = SessionRegistry(comms_dir)
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
            yield

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
    async def api_messages(room: str = DEFAULT_ROOM, limit: int = 200,
                           after: str | None = None,
                           before: str | None = None) -> dict[str, Any]:
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
    async def api_long_poll(room: str = DEFAULT_ROOM, after: str | None = None,
                            timeout: float = 15.0) -> dict[str, Any]:
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
    async def api_search(q: str = Query(..., min_length=1),
                         field: str | None = None,
                         room: str = DEFAULT_ROOM) -> dict[str, Any]:
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
        sessions._load()
        return {"sessions": [s.to_dict() for s in sessions.all()]}

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
        """Remove a session entirely (so it disappears from sidebar)."""
        sessions._load()
        if name not in sessions._sessions:
            raise HTTPException(404, f"no session named {name!r}")
        sessions._sessions.pop(name)
        sessions._save()
        await hub.broadcast({"kind": "session_removed", "name": name})
        return {"removed": name}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await hub.connect(websocket)
        try:
            snapshot = {
                "kind": "snapshot",
                "messages": stores[DEFAULT_ROOM].read_all()[-100:],
                "sessions": [s.to_dict() for s in sessions.all()],
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

    return app


def make_app(comms_dir: Path, rooms: list[str] | None = None):
    return create_app(comms_dir, rooms)


# Back-compat alias for tests/scripts that imported create_server.
create_server = create_app


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Run chatroom-mcp server + Web GUI.")
    p.add_argument("--comms-dir", type=Path, required=True)
    p.add_argument("--rooms", nargs="*", default=None,
                   help="room names (default: main). Multi-room via e.g. --rooms main ops research")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7777)
    a = p.parse_args()
    import uvicorn
    uvicorn.run(create_app(a.comms_dir, a.rooms), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
