# Roadmap

## MVP (Phase 1, done)

- [x] MCP server with 5 tools + 1 resource
- [x] Append-only JSONL store compatible with workbuddy-agent-comms v2.1
- [x] Session registry with callback URLs
- [x] Three-pane Textual TUI with @ routing
- [x] Tab to switch identity (workbuddy | opencode | human)
- [x] Push notifications on @-mention (fire-and-forget HTTP POST)
- [x] Web GUI (rebuildable `index.html`)
- [x] Tests (~20) + GitHub Actions CI (lint + test on linux/mac/windows, py 3.11/3.12)

## Phase 2 (done)

- [x] **SSE streaming** — `GET /api/stream` real-time push (message + session events),
      room-scoped, with 15s heartbeats (live channel for the web GUI; WS kept as a transport)
- [x] **Long-poll fallback** — `GET /api/messages/poll?room=&after=&timeout=`
      blocks until a new message or timeout (for clients without SSE/WS)
- [x] **Search / jump-to-message** — `GET /api/search?q=&field=&room=` +
      `chatroom_search` MCP tool + store.search; web search box filters live
- [x] **Theme configuration** — web light/dark toggle (persisted), TUI light palette
      + `Ctrl+T` toggle
- [x] **Multi-room support** — `Store` is per-room (`messages-<room>.jsonl`), server
      accepts `--rooms`, exposes `GET /api/rooms` + `chatroom_rooms`, and every
      read/write API + MCP tool takes a `room` param; TUI views a room via `--room`

## Phase 3 (next)

- [ ] Attachment / code-block preview (web already renders fenced code; TUI minimal)
- [ ] Voice messages (requires audio capture + storage; MCP schema extension)
- [ ] Mobile / web-push notifications (external providers: FCM/web-push)
- [ ] Cross-room routing across instances (multi-process message forwarding)

## Out of scope

- Agent runtime hosting (chatroom does not spawn agents; agents connect in)
- LLM integration (chatroom is message-passing only)
- Persisted chat history beyond what is already in messages.jsonl