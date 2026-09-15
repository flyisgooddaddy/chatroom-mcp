# Architecture

```
+----------------------+        +------------------------+        +----------+
|   agent (workbuddy)  |  MCP   |     chatroom-mcp       |  read   | messages |
|   or opencode        |<------>|     MCP server         |<------->| .jsonl   |
+----------------------+  HTTP  |     (FastAPI + mcp)    |  write  +----------+
                                  |                          ^
+----------------------+           |                          | poll
|   human (TUI)        |<--------->|   Textual TUI            |
|   @workbuddy ...     |   IPC    |   (3 panes + input)     |
+----------------------+           +--------------------------+
```

## Components

| Module | Role |
|---|---|
| `chatroom/protocol.py` | Schema validator (compatible with `workbuddy-agent-comms` v2.1) |
| `chatroom/store.py` | Append-only per-room JSONL store (`messages.jsonl` / `messages-<room>.jsonl`); assigns msg-NNNN ids; atomic via O_APPEND; full-text `search()` |
| `chatroom/sessions.py` | Agent session registry; persisted to `_sessions.json`. Each `Session` also carries `bound_session_id` (host conversation @ targets), `host_sessions` (selectable conversations the adapter reports) and `pending_command` (queued bind/create/unbind) |
| `chatroom/server.py` | MCP server (7 tools + 1 resource) over streamable-HTTP + REST/SSE/WS (room-aware) |
| `chatroom/push.py` | Async HTTP callback to agents; fire-and-forget |
| `chatroom/hub.py` | Broadcast hub: WebSocket clients + SSE `asyncio.Queue` subscribers |
| `chatroom/tui/app.py` | Textual three-pane chatroom with @ routing, theme toggle, search |
| `chatroom/cli.py` | argparse entry; starts server (bg thread) + TUI |

## Transport surface

| Endpoint | Method | Purpose |
|---|---|---|
| `/mcp` | POST | MCP streamable-HTTP (FastMCP) |
| `/` | GET | Web GUI |
| `/api/messages` | GET | list (`room`, `limit`, `after`) |
| `/api/messages/poll` | GET | long-poll (`room`, `after`, `timeout`) |
| `/api/search` | GET | full-text search (`q`, `field`, `room`) |
| `/api/stream` | GET | SSE real-time push (`room`) |
| `/api/rooms` | GET | list rooms |
| `/api/sessions` | GET | list sessions |
| `/api/post` | POST | post message (`room`) |
| `/api/sessions/{name}` | DELETE | kick session |
| `/api/agent/{name}/host` | POST | adapter reports selectable host sessions + binding (also a REST handshake) |
| `/api/agent/{name}/bind` | POST | user queues `bind` / `create` / `unbind` |
| `/api/agent/{name}/commands` | GET | adapter polls pending binding commands |
| `/api/agent/{name}/commands/ack` | POST | adapter confirms it handled the command |
| `/ws` | WS | WebSocket broadcast + snapshot (also emits `session_updated`) |

MCP tools: `chatroom_handshake`, `chatroom_pull`, `chatroom_post`
(`room`), `chatroom_history` (`room`), `chatroom_search` (`q`, `field`, `room`),
`chatroom_rooms`, `chatroom_sessions`. Resource: `chat://messages`.

## Message lifecycle

```
human types in TUI input
       |
       v
ChatroomApp._send_message(text)
       |
       v
Store.append(msg)   <--- assigns id="msg-0042", timestamp
       |
       v
messages.jsonl (append-only)
       |
       +--> ChatroomApp._poll_new()   (next interval, <2s)
       +--> server.call_tool("chatroom_post") from MCP clients
       +--> server checks `to` field; if matches a registered session
             with callback_url, fire notify() to that URL
```

## Storage

* Path: `<comms-dir>/messages.jsonl` (room `main`) or `<comms-dir>/messages-<room>.jsonl`
* Format: one JSON object per line, UTF-8, no BOM
* Messages carry a `room` field set by the server on append
* Validation: each message must satisfy v2.1 schema
  * `id` (assigned if missing)
  * `from` (sender name)
  * `type` ∈ {finding, plan, request, ack, requestion, done, blocked}
  * `timestamp` (ISO8601, assigned if missing)
  * `subject` (one-line summary)
  * `brief` required for {plan, request, done}
  * `in_reply_to` required for {done, ack, requestion}

## Real-time delivery preference

Web GUI prefers **SSE** (`/api/stream`), falls back to **WebSocket** (`/ws`),
then to the 2s **poll** when disconnected. `POST /api/post` broadcasts to all
three transports via `chatroom/hub.py`.

## Push flow

1. Agent calls `chatroom_handshake({"name": "workbuddy", "callback_url": "http://x"})`
   → server stores in `<comms-dir>/_sessions.json`
2. Human (or another agent) posts a message with `to="workbuddy"`
3. Server looks up session, finds callback URL, fires `httpx.AsyncClient.post(...)`
4. Agent's hook receives `{"event": "chatroom_message", "message": {...}}`
5. Agent calls `chatroom_pull` to fetch the full thread

If the callback fails (timeout / 4xx / 5xx), server retries up to N times
(default 1) and silently drops. Agents can always fall back to polling.

## Session binding protocol (user decides which host session gets @)

The server stores messages and reliably replies (①④), but it cannot know *which
host conversation carries the agent's context* — only the agent does (②). So the
chatroom exposes a generic REST protocol that agents' adapters implement, and the
**user** (not the agent) decides the binding:

```
        adapter (in host process)                     server
        -------------------------                     ------
  1.  POST /api/agent/{name}/host  ----------------->  registers session (auto-handshake)
                                        host_sessions + bound_session_id
  2.  user clicks "bind" in Web GUI                    POST /api/agent/{name}/bind
                                                        -> Session.pending_command = {op, session_id}
  3.  GET /api/agent/{name}/commands   <-------------  adapter polls for pending
  4.  adapter executes op in-process:
        bind   -> target host session = session_id
        create -> host session.create(...) (fallback: newest in dir)
        unbind -> clear binding (adapter auto-picks newest)
  5.  POST /api/agent/{name}/commands/ack ---------->  confirms; server sets bound_session_id
  6.  server broadcasts {kind:"session_updated"}        Web GUI refreshes the highlighted binding
```

`op` ∈ `{bind, create, unbind}`. `bind` carries `session_id`; `create` may carry
`directory`/`title`. The adapter then posts `@`-mentions into `bound_session_id`
via its host SDK (opencode: `client.session.prompt`, openwriter: `_emit(InboundMessage)`).

Key invariant: the adapter is a **distributed artifact of the chatroom** (e.g.
`examples/opencode-plugin/chatroom-bridge.ts`), and the binding protocol is a
**chatroom protocol**; the host is only its execution environment. This keeps
"each agent injects prompts differently" contained inside the adapter instead of
leaking up to the user's operating layer.
