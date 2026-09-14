"""MCP server tools (P1 stub: schemas only; dispatcher returns not-implemented)."""
from __future__ import annotations
from typing import Any

TOOL_HANDSHAKE = "chatroom_handshake"
TOOL_PULL = "chatroom_pull"
TOOL_POST = "chatroom_post"
TOOL_HISTORY = "chatroom_history"
TOOL_SESSIONS = "chatroom_sessions"
RESOURCE_MESSAGES = "chat://messages"


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {"name": TOOL_HANDSHAKE, "description": "Register or refresh an agent session.",
         "inputSchema": {"type": "object",
                         "properties": {"name": {"type": "string"},
                                        "callback_url": {"type": "string"}},
                         "required": ["name"]}},
        {"name": TOOL_PULL, "description": "Pull messages since a given id (or all if omitted).",
         "inputSchema": {"type": "object",
                         "properties": {"since": {"type": "string"},
                                        "limit": {"type": "integer", "default": 50}}}},
        {"name": TOOL_POST, "description": "Post a message.",
         "inputSchema": {"type": "object",
                         "properties": {"msg": {"type": "object"}},
                         "required": ["msg"]}},
        {"name": TOOL_HISTORY, "description": "Return the most recent N messages.",
         "inputSchema": {"type": "object",
                         "properties": {"limit": {"type": "integer", "default": 50}}}},
        {"name": TOOL_SESSIONS, "description": "List known agent sessions.",
         "inputSchema": {"type": "object", "properties": {}}},
    ]


def resource_schemas() -> list[dict[str, Any]]:
    return [{"uri": RESOURCE_MESSAGES,
             "name": "chatroom messages",
             "description": "Stream of all messages (subscribe for live updates).",
             "mimeType": "application/x-ndjson"}]


async def dispatch(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """P1 stub: real dispatcher is added in P2."""
    return {"error": {"code": -32601, "message": f"tool {name!r} not implemented (P1 stub)"}}
