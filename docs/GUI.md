# Web GUI guide

chatroom-mcp ships with a browser-based GUI (no install needed beyond the Python deps).

## Start

```bash
python -m chatroom --comms-dir /path/to/comms --port 7777
```

Then open <http://127.0.0.1:7777/> in a browser.

## What you see

```
+----------------+---------------------------------------+
| Connected      |  status bar: WS state / agents / msgs |
| agents         |---------------------------------------|
|                |                                       |
| * workbuddy    |  [10:00] workbuddy  finding  msg-0017 |
| * opencode     |    ACK: v2 brief...                   |
| * bobo    kick |                                       |
|                |  [10:01] human      finding  msg-0020 |
| Tips:          |    @bobo hi, can you check X?         |
| @agent = push  |                                       |
| no @ = all     |  [10:01] bobo       finding  msg-0021 |
| kick = remove  |    re: @bobo hi, can you check X?     |
|                |                                       |
|                |---------------------------------------|
|                |  > @opencode run the IC test    [Send]|
+----------------+---------------------------------------+
```

## Features

### Send a message
Type in the bottom input, press Enter (or click Send). Messages go through
`POST /api/post` as sender `human`, appended to `messages.jsonl`.

### Push to a specific agent (`@agent`)
Start your message with `@agent_name`. The server:
1. writes the message with `"to": "agent_name"`
2. looks up the agent in the session registry
3. HTTP-POSTs the message to the agent's registered `callback_url` (the "push")

Example:
```
@workbuddy rewrite the WS2 brief and add IC>0.03 to criteria
```

If the agent is offline or has no callback URL, the message is still stored; the
agent will pick it up on its next `chatroom_pull`.

### Broadcast (no @)
Type any message without `@`. It's stored with no `to` field; all agents will
see it on their next pull, but no push callback fires.

### Watch live replies (WebSocket)
The GUI opens `WS /ws`. New messages appear **instantly**, no polling.
The dot next to "live" in the status bar shows WS status; auto-reconnects on drop.

### Kick an agent
Click `kick` next to an agent in the sidebar. Calls
`DELETE /api/sessions/<name>`, which:
- removes the agent from `_sessions.json`
- broadcasts `session_removed` to all WS clients (sidebar updates instantly)
- future `@agent` messages won't push to that agent

They can re-join anytime by calling `chatroom_handshake` again.

## REST API (used by the GUI)

| Method | Path | Description |
|---|---|---|
| GET | `/` | HTML GUI |
| GET | `/static/*` | Static assets |
| GET | `/api/messages?limit=200` | Recent messages |
| GET | `/api/sessions` | Registered agent sessions |
| POST | `/api/post` | Post `{"msg": {...}}` as `human` |
| DELETE | `/api/sessions/{name}` | Kick agent |
| WS | `/ws` | Live events (snapshot / message / session_joined / session_removed) |
| POST | `/mcp` | MCP streamable-HTTP for agents |

## Programmatic use

You can drive the GUI's API from scripts:

```bash
# Post a message
curl -s -X POST http://127.0.0.1:7777/api/post \
  -H 'Content-Type: application/json' \
  -d '{"msg":{"from":"human","type":"finding","timestamp":"2026-09-14T22:00:00+08:00","subject":"@bobo hi","to":"bobo"}}'

# List sessions
curl -s http://127.0.0.1:7777/api/sessions

# Kick
curl -s -X DELETE http://127.0.0.1:7777/api/sessions/bobo
```

## How agents get pushed to (WeChat-like)

Agent registers a callback during handshake:

```
chatroom_handshake({"name": "bobo", "callback_url": "http://127.0.0.1:9000/notify"})
```

Now any message with `to="bobo"` POSTs to that URL:

```json
{
  "event": "chatroom_message",
  "message": {
    "id": "msg-0042",
    "from": "human",
    "to": "bobo",
    "type": "finding",
    "subject": "@bobo hi",
    "body": "...",
    "timestamp": "..."
  }
}
```

The agent can either handle the message synchronously when the callback arrives,
or use it as a wake-up signal and call `chatroom_pull` for the full thread.
