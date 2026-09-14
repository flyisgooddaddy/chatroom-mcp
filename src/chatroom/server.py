"""MCP server + Web GUI (FastMCP 1.x + FastAPI routes).

Serves:
  POST /mcp           MCP streamable-HTTP (FastMCP)
  GET  /              Web GUI (index.html)
  GET  /static/*      JS / CSS / favicon
  GET  /api/messages  JSON list of messages
  GET  /api/sessions  JSON list of sessions
  POST /api/post      Post a message from the GUI (human sender)
  DELETE /api/sessions/{name}  Kick a session
  WS   /ws            WebSocket: broadcasts new messages + session events
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP

from chatroom.hub import hub
from chatroom.protocol import is_well_formed
from chatroom.sessions import SessionRegistry
from chatroom.store import Store


TOOL_HANDSHAKE = "chatroom_handshake"
TOOL_PULL = "chatroom_pull"
TOOL_POST = "chatroom_post"
TOOL_HISTORY = "chatroom_history"
TOOL_SESSIONS = "chatroom_sessions"
RESOURCE_MESSAGES = "chat://messages"

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_DIR = WEB_DIR / "static"


def _msg_id_key(msg_id: str) -> int:
    try:
        return int(msg_id.split("-", 1)[1])
    except (IndexError, ValueError):
        return 0


async def _notify_and_broadcast(stored: dict[str, Any], sessions) -> None:
    """Fire push to @-target agent (best-effort) + broadcast to WS clients."""
    target = stored.get("to")
    if target:
        # Reload sessions from disk: agents may have registered in other processes
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


def _build_mcp(comms_dir: Path, store=None, sessions=None) -> FastMCP:
    """Build the FastMCP instance + register tools. Reads/writes via store."""
    if store is None:
        store = Store(comms_dir)
    if sessions is None:
        sessions = SessionRegistry(comms_dir)

    mcp = FastMCP(
        name="chatroom-mcp",
        instructions=(
            "A shared chatroom for AI agents and humans. "
            "Use chatroom_handshake to register, chatroom_pull to fetch, "
            "chatroom_post to send. Web GUI runs at GET /."
        ),
    )

    @mcp.tool(name=TOOL_HANDSHAKE, description="Register or refresh an agent session.")
    async def handshake(name: str, callback_url: str | None = None) -> dict[str, Any]:
        sess = sessions.handshake(name=name, callback_url=callback_url)
        await hub.broadcast({"kind": "session_joined", "session": sess.to_dict()})
        return sess.to_dict()

    @mcp.tool(name=TOOL_PULL, description="Pull messages newer than `since`. Empty/null for all.")
    def pull(since: str | None = None, limit: int = 50) -> dict[str, Any]:
        all_msgs = store.read_all()
        if since:
            cutoff = _msg_id_key(since)
            out = [m for m in all_msgs if _msg_id_key(str(m.get("id", ""))) > cutoff]
        else:
            out = list(all_msgs)
        return {"messages": out[-limit:]}

    @mcp.tool(name=TOOL_POST, description="Post a new message. Pushes to @-target agent if registered.")
    async def post(msg: dict[str, Any]) -> dict[str, Any]:
        try:
            stored = store.append(msg)
        except ValueError as e:
            return {"error": {"code": -32602, "message": str(e)}}
        await _notify_and_broadcast(stored, sessions)
        return stored

    @mcp.tool(name=TOOL_HISTORY, description="Return the most recent N messages (default 50, max 1000).")
    def history(limit: int = 50) -> dict[str, Any]:
        limit = max(1, min(limit, 1000))
        return {"messages": store.read_all()[-limit:]}

    @mcp.tool(name=TOOL_SESSIONS, description="List known agent sessions.")
    def list_sessions() -> dict[str, Any]:
        return {"sessions": [s.to_dict() for s in sessions.all()]}

    @mcp.resource(uri=RESOURCE_MESSAGES, name="chatroom messages",
                  description="All messages as NDJSON.",
                  mime_type="application/x-ndjson")
    def messages_resource() -> str:
        return "\n".join(json.dumps(m, ensure_ascii=False) for m in store.read_all())

    return mcp


def create_app(comms_dir: Path) -> FastAPI:
    """Build the full ASGI app (MCP + Web GUI + WS)."""
    comms_dir = Path(comms_dir)
    store = Store(comms_dir)
    sessions = SessionRegistry(comms_dir)
    mcp = _build_mcp(comms_dir, store=store, sessions=sessions)

    app = FastAPI(title="chatroom-mcp", version="0.2.0")
    # Expose for tests / debugging
    app.state.sessions = sessions
    app.state.store = store
    app.state.mcp = mcp

    # --- Static GUI ---
    @app.get("/", include_in_schema=False)
    async def index():
        idx = WEB_DIR / "index.html"
        if not idx.exists():
            return JSONResponse({"error": "GUI not built; see src/chatroom/web/"}, status_code=500)
        return FileResponse(idx)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # --- REST API for the GUI ---
    @app.get("/api/messages")
    async def api_messages(limit: int = 200) -> dict[str, Any]:
        msgs = store.read_all()[-max(1, min(limit, 1000)):]
        return {"messages": msgs}

    @app.get("/api/sessions")
    async def api_sessions() -> dict[str, Any]:
        sessions._load()  # pick up agents registered by other processes
        return {"sessions": [s.to_dict() for s in sessions.all()]}

    @app.post("/api/post")
    async def api_post(payload: dict[str, Any]) -> dict[str, Any]:
        """GUI posts a message as 'human'. Auto-fills id/timestamp."""
        msg = payload.get("msg")
        if not isinstance(msg, dict):
            raise HTTPException(400, "msg must be a dict")
        msg.setdefault("from", "human")
        try:
            stored = store.append(msg)
        except ValueError as e:
            raise HTTPException(400, str(e))
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

    # --- WebSocket for live updates ---
    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await hub.connect(websocket)
        try:
            # Send initial snapshot
            snapshot = {
                "kind": "snapshot",
                "messages": store.read_all()[-100:],
                "sessions": [s.to_dict() for s in sessions.all()],
            }
            try:
                await websocket.send_text(json.dumps(snapshot, ensure_ascii=False))
            except Exception:
                pass
            while True:
                # Keep alive; we only push from server side
                msg = await websocket.receive_text()
                if msg == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(websocket)

    # Mount MCP streamable HTTP at /mcp
    mcp_app = mcp.streamable_http_app()
    app.mount("/mcp", mcp_app)

    return app


def make_app(comms_dir: Path):
    return create_app(comms_dir)


# Back-compat alias for tests/scripts that imported create_server.
create_server = create_app


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Run chatroom-mcp server + Web GUI.")
    p.add_argument("--comms-dir", type=Path, required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7777)
    a = p.parse_args()
    import uvicorn
    uvicorn.run(create_app(a.comms_dir), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
