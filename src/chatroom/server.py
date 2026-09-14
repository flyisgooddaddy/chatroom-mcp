"""MCP server (FastMCP): 5 tools + 1 resource, streamable-HTTP transport."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from chatroom.protocol import is_well_formed
from chatroom.sessions import SessionRegistry
from chatroom.store import Store


TOOL_HANDSHAKE = "chatroom_handshake"
TOOL_PULL = "chatroom_pull"
TOOL_POST = "chatroom_post"
TOOL_HISTORY = "chatroom_history"
TOOL_SESSIONS = "chatroom_sessions"
RESOURCE_MESSAGES = "chat://messages"


def _msg_id_key(msg_id: str) -> int:
    try:
        return int(msg_id.split("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def create_server(comms_dir: Path) -> FastMCP:
    comms_dir = Path(comms_dir)
    store = Store(comms_dir)
    sessions = SessionRegistry(comms_dir)

    mcp = FastMCP(
        name="chatroom-mcp",
        instructions=(
            "A shared chatroom for AI agents and humans. "
            "Use chatroom_handshake to register, chatroom_pull to fetch, "
            "chatroom_post to send. Subscribe to chat://messages for live updates."
        ),
    )

    @mcp.tool(name=TOOL_HANDSHAKE, description="Register or refresh an agent session. Returns session_id.")
    def handshake(name: str, callback_url: str | None = None) -> dict[str, Any]:
        sess = sessions.handshake(name=name, callback_url=callback_url)
        return sess.to_dict()

    @mcp.tool(name=TOOL_PULL, description="Pull messages newer than `since` (msg-NNNN id). Empty/null for all.")
    def pull(since: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        all_msgs = store.read_all()
        if since:
            cutoff = _msg_id_key(since)
            out = [m for m in all_msgs if _msg_id_key(str(m.get("id", ""))) > cutoff]
        else:
            out = list(all_msgs)
        return out[-limit:]

    @mcp.tool(name=TOOL_POST, description="Post a new message. Assigns id+timestamp if missing.")
    def post(msg: dict[str, Any]) -> dict[str, Any]:
        try:
            stored = store.append(msg)
        except ValueError as e:
            return {"error": {"code": -32602, "message": str(e)}}
        # Push notification (fire-and-forget)
        target = stored.get("to")
        if target:
            sess = sessions.get(target)
            if sess and sess.callback_url:
                try:
                    from chatroom.push import notify_fire_and_forget
                    notify_fire_and_forget(sess.callback_url, {
                        "event": "chatroom_message",
                        "message": stored,
                    })
                except Exception:
                    pass
        return stored

    @mcp.tool(name=TOOL_HISTORY, description="Return the most recent N messages (default 50, max 1000).")
    def history(limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        return store.read_all()[-limit:]

    @mcp.tool(name=TOOL_SESSIONS, description="List known agent sessions.")
    def list_sessions() -> list[dict[str, Any]]:
        return [s.to_dict() for s in sessions.all()]

    @mcp.resource(uri=RESOURCE_MESSAGES, name="chatroom messages",
                  description="All messages as NDJSON. Subscribe for live updates.",
                  mime_type="application/x-ndjson")
    def messages_resource() -> str:
        lines = [json.dumps(m, ensure_ascii=False) for m in store.read_all()]
        return "\n".join(lines)

    return mcp


def make_app(comms_dir: Path):
    return create_server(comms_dir).streamable_http_app()


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Run chatroom MCP server.")
    p.add_argument("--comms-dir", type=Path, required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7777)
    a = p.parse_args()
    mcp = create_server(a.comms_dir)
    mcp.settings.host = a.host
    mcp.settings.port = a.port
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
