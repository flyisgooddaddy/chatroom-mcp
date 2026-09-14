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
| `chatroom/store.py` | Append-only JSONL store; assigns msg-NNNN ids; atomic via O_APPEND |
| `chatroom/sessions.py` | Agent session registry; persisted to `_sessions.json` |
| `chatroom/server.py` | MCP server (5 tools + 1 resource) over streamable-HTTP |
| `chatroom/push.py` | Async HTTP callback to agents; fire-and-forget |
| `chatroom/tui/app.py` | Textual three-pane chatroom with @ routing |
| `chatroom/cli.py` | argparse entry; starts server (bg thread) + TUI |

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

* Path: `<comms-dir>/messages.jsonl`
* Format: one JSON object per line, UTF-8, no BOM
* Validation: each message must satisfy v2.1 schema
  * `id` (assigned if missing)
  * `from` (sender name)
  * `type` ∈ {finding, plan, request, ack, requestion, done, blocked}
  * `timestamp` (ISO8601, assigned if missing)
  * `subject` (one-line summary)
  * `brief` required for {plan, request, done}
  * `in_reply_to` required for {done, ack, requestion}

## Push flow

1. Agent calls `chatroom_handshake({"name": "workbuddy", "callback_url": "http://x"})`
   → server stores in `<comms-dir>/_sessions.json`
2. Human (or another agent) posts a message with `to="workbuddy"`
3. Server looks up session, finds callback URL, fires `httpx.AsyncClient.post(...)`
4. Agent's hook receives `{"event": "chatroom_message", "message": {...}}`
5. Agent calls `chatroom_pull` to fetch the full thread

If the callback fails (timeout / 4xx / 5xx), server retries up to N times
(default 1) and silently drops. Agents can always fall back to polling.
