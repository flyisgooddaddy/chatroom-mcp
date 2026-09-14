# Roadmap

## MVP (Phase 1, done)

- [x] MCP server with 5 tools + 1 resource
- [x] Append-only JSONL store compatible with workbuddy-agent-comms v2.1
- [x] Session registry with callback URLs
- [x] Three-pane Textual TUI with @ routing
- [x] Tab to switch identity (workbuddy | opencode | human)
- [x] Push notifications on @-mention (fire-and-forget HTTP POST)
- [x] 31 tests passing
- [x] GitHub Actions CI (lint + test on linux/mac/windows, py 3.11/3.12)

## Phase 2 (next)

- [ ] SSE streaming for `chat://messages` resource (real-time push without polling)
- [ ] Long-poll fallback (when SSE unavailable)
- [ ] Search / jump-to-message
- [ ] Theme configuration (light / dark / custom colors)
- [ ] Multi-room support (multiple chatroom-mcp instances)
- [ ] In-TUI attachment preview (images, code blocks)

## Phase 3

- [ ] Voice messages
- [ ] Web UI as alternative to TUI
- [ ] Mobile notifications (push to phone)
- [ ] Cross-room routing (rooms in different processes)

## Out of scope

- Agent runtime hosting (chatroom does not spawn agents; agents connect in)
- LLM integration (chatroom is message-passing only)
- Persisted chat history beyond what is already in messages.jsonl
