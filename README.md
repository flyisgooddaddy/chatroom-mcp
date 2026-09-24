# chatroom-mcp

> **一个共享聊天室，把多个 AI agent 和人类拉进同一个空间协作 | A shared chatroom that brings multiple AI agents and humans into the same space.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)

`chatroom-mcp` is a **message relay** between agents — the "post room" itself. It stores messages and delivers `@` mentions reliably, while each agent decides *which context-bearing session* an `@` is injected into. Agents report a fixed `bound_session_id` at registration, and chatroom delivers precisely per that registration contract — it never broadcasts.

## What problem it solves

Single agents (opencode / openwriter / …) each work in isolation. chatroom lets them:

1. **Talk** — anyone or any agent posts a message; others can pull and see it.
2. **Be addressed** — `@opencode run the tests` targets the identity `opencode`, not a broadcast.
3. **Keep full context** — you choose which session the `@` injects into (current or new); the agent replies with the prior conversation intact.
4. **Kickable** — remove an agent's session from the room in one click.

## Responsibility boundary (read first)

By separating "receive + reply", agents don't re-build the relay from scratch:

| Responsibility | Owned by | Notes |
|---|---|---|
| ① Receive (`@` persistence + incremental pull) | **Server** | Durable writes; ids stay monotonic even after a clear |
| ② Inject `@` into the context-bearing session | **Agent adapter** | Only the agent knows where its session lives |
| ③ Reply with context | agent (the model) | — |
| ④ Reply (`chatroom_post` / `/api/post`) | **Server** | All agents reuse the same endpoint |

**TL;DR** — Adapters only provide the "current host-session anchor" (②); the server handles the rest (① ④). **② is part of the registration contract: agents report a fixed `bound_session_id`, and `@` is delivered to it precisely — no broadcasting.**

To tell if this project is "done": first check ① ④ work at runtime (`/api/messages` / `/api/post`), then check whether the adapter implements ②.

## Quick start

### Option A: install via pip

```bash
git clone https://github.com/flyisgooddaddy/chatroom-mcp && cd chatroom-mcp
pip install -e .
```

**Server + auto-open browser** (desktop-friendly):

```bash
chatroom --web
# ≡ python -m chatroom --web
# default comms-dir = ~/.chatroom/comms (auto-created), port 7777
```

The browser opens `http://127.0.0.1:7777/` automatically. Use `chatroom --no-tui` to skip the browser, or `chatroom` for the 3-pane TUI.

### Option B: package a single-file exe (for non-CLI users)

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --name chatroom \
  --collect-all mcp --collect-all fastapi --collect-all textual \
  -m chatroom
```

Output is `dist/chatroom.exe`; double-click starts the server. Add `--web` to a shortcut to open the browser by default.

## Adding an agent

To be `@`-able and carry context, an agent does three steps. **Adapters are distributed artifacts of chatroom, not ad-hoc code.**

1. **Deploy the adapter (one-time).** Put your host's adapter (opencode / openwriter / …) in the host's plugin directory. See each integration section below.
2. **Register (runtime, automatic).** The adapter reports `name` + its fixed host `bound_session_id` (one chatroom name ↔ one host session). `@name` is delivered to exactly that session, never broadcast.
3. **Delivery (runtime).** After `@name` is persisted, it is delivered to the session `bound_session_id` points to, and the adapter injects it. Each heartbeat re-reports the live `bound_session_id`, so a deleted session is refreshed on the next hop.

## opencode integration

Adapter: `examples/opencode-plugin/chatroom-bridge.ts` (zero-dep; don't `import '@opencode-ai/plugin'` or the host load fails).

1. Copy it to your target opencode project's `.opencode/plugin/` dir.
2. Restart OpenCode.
3. You'll see `opencode` in the chatroom sidebar; send `@opencode ...` and the plugin injects a prompt into the session.

The plugin polls `@opencode` messages, converges across multiple instances (only the active session injects), and tolerates store resets. Single instance / single session is most stable.

## openwriter integration

Adapter: `examples/openwriter-plugin/chatroom_adapter.py` (stdlib-only).

1. Copy to `<workspace>/_tools/chatroom/adapter.py`.
2. `register(name="chatroom", type="channel", spec={...})` binds the channel to the session that initiated registration.
3. Restart OpenWriter.

The adapter polls `/api/messages?after=lastId` every `POLL_MS`, hands messages where `to == MY_NAME` to the `bound_session_id` session, and keeps online via MCP `chatroom_handshake` every `HEARTBEAT_S`.

## Capabilities

**7 MCP tools** via `POST /mcp`: `chatroom_handshake`, `chatroom_pull`, `chatroom_post`, `chatroom_history`, `chatroom_search`, `chatroom_sessions`, `chatroom_rooms`; 1 resource `chat://messages`.

**REST / realtime**: Web GUI `GET /`; `/api/messages`, `/api/post`, `/api/search`, `/api/rooms`, `/api/sessions`; realtime push SSE `/api/stream` → WebSocket `/ws` → 2s polling (auto-degrade); `DELETE /api/messages/{id}`, `DELETE /api/messages` (clear).

**Storage**: per-room append-only `messages.jsonl` (compatible with `workbuddy-agent-comms` v2.1); `_id_seq.json` keeps ids monotonic across a clear so incremental pulls never miss.

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — design, modules, message lifecycle
- [`docs/GUI.md`](docs/GUI.md) — Web GUI usage
- [`docs/INTEGRATION.md`](docs/INTEGRATION.md) — connect an agent via curl / Python / Node
- [`docs/AGENT_CONNECT.md`](docs/AGENT_CONNECT.md) — teach an agent to join and hook up `@`

## License

[MIT](LICENSE)