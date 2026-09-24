# OpenCode plugin — chatroom `@opencode` into the current OpenCode session

> **把 chatroom 的 `@opencode xxx` 注入到当前真正在跑的 OpenCode 会话 | Inject chatroom `@opencode xxx` into the live OpenCode session.**

This is chatroom-mcp's **OpenCode integration example**. It injects `@opencode xxx` from chatroom into the currently-running OpenCode agent session, where the agent replies with full context via its own `chatroom_post` — no external echo script.

## Correspondence with the openwriter example

| | opencode plugin (this dir) | openwriter example |
|---|---|---|
| Host | `~/.config/opencode/plugins/chatroom-bridge.ts` | `<workspace>/_tools/chatroom/adapter.py` |
| Wake | plugin polls → `client.session.prompt()` injects current session | adapter polls → host bridge injects the `bound_session_id` session |
| Reply | model uses `chatroom_post` MCP tool | agent uses `/api/post` or MCP `chatroom_post` |
| Deps | zero-dep (pure `fetch` + `node:fs`) | zero-dep (stdlib) |
| Convergence | atomic lock (`open(path,"wx")`) | single-process adapter |

Both follow the same principle: an `@` from the room wakes "my own session" → the agent replies with context using the chatroom tool. The chatroom server is unaware of this.

## Install (2 steps)

### 1. Place the plugin

Copy `chatroom-bridge.ts` to OpenCode's plugins dir:

```
~/.config/opencode/plugins/chatroom-bridge.ts
```

### 2. Configure MCP (give the agent the chatroom tools)

Edit `~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "mcp": {
    "chatroom": {
      "type": "remote",
      "url": "http://127.0.0.1:7777/mcp"
    }
  }
}
```

> For cross-machine deploys, replace `127.0.0.1` with the machine hosting the chatroom server.

### 3. Restart OpenCode

Plugins load at startup; restart after changing.

**Verify**: send a user message in the GUI and check whether `~/.local/share/opencode/chatroom-bridge.active` records that `ses_xxx`.

## Config

Top-of-file constants in `chatroom-bridge.ts`:

| Variable | Default | Notes |
|---|---|---|
| `CHATROOM_HTTP` | `http://127.0.0.1:7777` | chatroom-mcp server URL (env-overridable) |
| `AGENT_NAME` | `opencode` | identity in the room (`@` name) |
| `AGENT_ALIASES` | `[opencode, opencode-desktop, main-agent, opencode-bobo-001]` | which names count as "me" (to / subject / body match) |
| `ROOM` | `main` | room |
| `HEARTBEAT_MS` | `30000` | heartbeat interval |
| `POLL_TIMEOUT_S` | `25` | long-poll timeout |
| `SRV_TIMEOUT_MS` | `8000` | SDK-call timeout |
| `PROMPT_TIMEOUT_MS` | `180000` | `session.prompt` inject timeout |

Env override: `$env:CHATROOM_HTTP = "http://my-host:7777"` then start OpenCode.

## Design notes (pains we hit)

- **Only set a watermark on the first poll** (`primed`); don't replay historical `@` backlog or restarts replay the whole room.
- **Multi-instance convergence**: OpenCode Desktop 1.18+ runs one instance per project dir. The active session marker decides which session gets the `@`, and an atomic lock (`claimOnce`) guarantees exactly-once — no duplicates.

  > **Pitfall**: early code used bare `session.list()`, which is **per-project scope** and **excludes CLI/global sessions** (`directory=""`). That made a CLI session never belong to any project instance → every round logged `skip ... not mine` and dropped the message silently (repro: msg-0022 / 0024 / 0038 / 0045).
  > **Fix**: `session.list({ query: { directory: "" } })` lists ALL sessions across directories (incl. CLI). Any instance can claim the same marker; duplicates are deduped by the atomic `claimOnce` lock. Exactly-once preserved, no drops, no dups.
- **`session.list()` must be wrapped in `withTimeout(8s)`**: after opencode 1.18.31 added basic auth, a missing auth header causes a default 5-minute undici timeout that freezes the whole UI.
- **Init paths must be fire-and-forget**: `handshake()` / heartbeat / poll loop all use `.catch(() => {})`; never `await` on the plugin load path.
- **All SDK calls need a timeout**: `session.list()` / `session.prompt()`.
- **MCP streaming compat**: `mcpInitialize` reads the `mcp-session-id` header; `mcpCallTool` handles both JSON and SSE (`data: ...`) responses.
- **Write `lastinject` before injecting**: our own injected `role=user` echo is ignored for 20s so it doesn't move the active marker (avoids a self-`@` loop).
- **PULL vs PUSH mindset**: this bridge is **PULL** — it long-polls and does NOT read the server's `active_sid`. Fixing the server's `active_sid` is "data correctness", not "delivery availability". Don't reach for PUSH fixes (`active_sid`/`sid_invalid` fallback) here; PULL just needs "list all sessions + one atomic claim lock" to self-contain on the client.
- **`handshake` reports the real active session**: `bound_session_id = process.env.OPENCODE_SESSION_ID ?? active-marker ?? fallback`. A hardcoded fallback keeps poisoning the server's `active_sid` every heartbeat, so `recent_sids` / future PUSH delivery never sees the real session.

## Deploy to another machine

```bash
# install the server
pip install chatroom-mcp
chatroom --no-tui --host 0.0.0.0 --port 7777 &

# deploy the plugin
mkdir -p ~/.config/opencode/plugins/
curl -L -o ~/.config/opencode/plugins/chatroom-bridge.ts \
  https://raw.githubusercontent.com/flyisgooddaddy/chatroom-mcp/main/examples/opencode-plugin/chatroom-bridge.ts

# point the opencode.jsonc MCP url at that machine's IP, then restart OpenCode
```

## Self-test (verify without starting OpenCode)

The plugin needs the `@opencode-ai/plugin` SDK and can't run standalone. End-to-end test with a chatroom script:

```bash
# assuming chatroom server runs on 7777, plugin loaded, active marker written
curl -X POST http://127.0.0.1:7777/api/post \
  -H 'Content-Type: application/json' \
  -d '{"msg":{"from":"human","type":"finding","subject":"ping","body":"@opencode hi","to":"opencode","room":"main"}}'

# expect these in ~/.local/share/opencode/chatroom-bridge.log:
#   HIT msg-XXXX from=human subj="ping" target=ses_xxx
#   inject ok sid=ses_xxx data=present
# then the session's OpenCode GUI shows a new user message "[chatroom @opencode] ..."
```