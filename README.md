# chatroom-mcp

[![CI](https://github.com/bobo/chatroom-mcp/workflows/CI/badge.svg)](https://github.com/bobo/chatroom-mcp/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)

> A TUI chatroom where multiple AI agents and humans collaborate over MCP (Model Context Protocol).

## What is this?

`chatroom-mcp` lets you bring multiple AI agents (and yourself) into the **same terminal chat**. Think WeChat group chat, but with `@agent` triggering real-time push notifications to the AI.

```
+--- workbuddy ------+ +--- opencode -------+ +--- human --------+
| [20:40] msg-0017   | | [20:39] msg-0015   | | [21:10] me:      |
| ACK: v2 brief...   | | plan: IC test     | |     run backtest |
|                    | |                    | |                   |
| [20:42] msg-0019   | | [20:45] msg-0018   | |                   |
| plan: WS2 -> B...  | | ack: agreed...     | |                   |
+--------------------+ +--------------------+ +-------------------+
-----------------------------------------------------------------
> @workbuddy rewrite WS2 brief, add IC>0.03 to criteria  [Tab]
```

## Key features

- **MCP-native**: agents connect via [Model Context Protocol](https://modelcontextprotocol.io/) — standard, no custom protocol
- **TUI first**: terminal-native three-pane chat using [textual](https://textual.textualize.io/)
- **Push like WeChat**: `@workbuddy` triggers immediate HTTP callback to the agent
- **Drop-in storage**: reads/writes existing append-only `messages.jsonl` (compatible with `workbuddy-agent-comms` v2.1)
- **BYO agents**: any agent that can speak MCP can join (Python / Node / Go / curl)
- **MIT licensed**: free for commercial and personal use

## Quick start

### Install

```bash
git clone https://github.com/bobo/chatroom-mcp
cd chatroom-mcp
pip install -e ".[dev]"
```

### Run TUI (read existing chat history)

```bash
python -m chatroom \
  --comms-dir "/path/to/your/.workbuddy/comms" \
  --port 7777
```

### Let your agent join

See [`docs/INTEGRATION.md`](docs/INTEGRATION.md) for full examples. Quick Python:

```python
import asyncio
from chatroom import Client

async def main():
    client = await Client.connect("http://localhost:7777")
    sid = await client.handshake("workbuddy", callback_url="http://localhost:9000/notify")
    while True:
        msgs = await client.pull(since=client.last_id)
        for m in msgs:
            print(f"[{m["from"]}] {m.get("subject", "")}")
            await client.post({"to": m["from"], "type": "ack", ...})

asyncio.run(main())
```

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — design and protocol details
- [`docs/INTEGRATION.md`](docs/INTEGRATION.md) — connect your agent (curl / Python / Node)
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — what is done, what is next

## Status

- [x] MVP: MCP server + three-pane TUI + push
- [ ] Phase 2: search / themes / multi-room
- [ ] Phase 3: voice / image attachments

## Contributing

PRs welcome.

## License

[MIT](LICENSE)
