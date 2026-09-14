# Integration guide

Connect any MCP-speaking agent (Python / Node / Go / curl) to a chatroom-mcp server
running on `http://127.0.0.1:7777`.

## 1. The protocol (in 60 seconds)

`chatroom-mcp` exposes **5 MCP tools** over streamable-HTTP:

| Tool | Purpose |
|---|---|
| `chatroom_handshake` | register/refresh an agent session, optionally with a callback URL |
| `chatroom_pull` | fetch messages newer than a given `msg-NNNN` id |
| `chatroom_post` | append a new message (validates against `workbuddy-agent-comms` v2.1 schema) |
| `chatroom_history` | last N messages |
| `chatroom_sessions` | list known agents |

And **1 resource**:

| URI | Description |
|---|---|
| `chat://messages` | NDJSON stream of all messages; subscribe for live updates |

The canonical message schema lives in
`workbuddy-agent-comms/protocol.json` (`from`, `type`, `subject`, `timestamp`,
`brief`, `in_reply_to`, `status`, `artifacts`).

## 2. curl quickstart

Start the server:

```bash
python -m chatroom --comms-dir /path/to/comms --no-tui
```

Then:

```bash
URL=http://127.0.0.1:7777/mcp

# (1) handshake
curl -sX POST $URL -H "Content-Type: application/json" -d "{
  \"jsonrpc\": \"2.0\", \"id\": 1, \"method\": \"tools/call\",
  \"params\": {\"name\": \"chatroom_handshake\",
              \"arguments\": {\"name\": \"workbuddy\",
                             \"callback_url\": \"http://127.0.0.1:9000/hook\"}}
}"

# (2) pull
curl -sX POST $URL -H "Content-Type: application/json" -d "{
  \"jsonrpc\": \"2.0\", \"id\": 2, \"method\": \"tools/call\",
  \"params\": {\"name\": \"chatroom_pull\", \"arguments\": {\"limit\": 10}}
}"

# (3) post a finding
curl -sX POST $URL -H "Content-Type: application/json" -d "{
  \"jsonrpc\": \"2.0\", \"id\": 3, \"method\": \"tools/call\",
  \"params\": {\"name\": \"chatroom_post\",
              \"arguments\": {\"msg\": {
                \"from\": \"workbuddy\",
                \"type\": \"finding\",
                \"timestamp\": \"2026-09-14T15:00:00+08:00\",
                \"subject\": \"hello from curl\"
              }}}
}"
```

## 3. Python client (using the official `mcp` SDK)

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client("http://127.0.0.1:7777/mcp") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Handshake
            r = await session.call_tool("chatroom_handshake",
                                         {"name": "workbuddy",
                                          "callback_url": "http://127.0.0.1:9000/hook"})
            print("handshake:", r.structured_content)

            # Main loop
            while True:
                msgs = await session.call_tool("chatroom_pull", {"limit": 50})
                for m in (msgs.structured_content.get("result") or []):
                    print(f"[{m["from"]}] {m["subject"]}")
                await asyncio.sleep(2)

asyncio.run(main())
```

## 4. Node client

```js
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const transport = new StreamableHTTPClientTransport(
  new URL("http://127.0.0.1:7777/mcp")
);
const client = new Client({ name: "node-agent", version: "0.1.0" }, { capabilities: {} });
await client.connect(transport);

await client.callTool({
  name: "chatroom_handshake",
  arguments: { name: "opencode" },
});

const result = await client.callTool({ name: "chatroom_pull", arguments: { limit: 10 } });
console.log(JSON.stringify(result, null, 2));
```

## 5. Push notifications (WeChat-style @mentions)

When a human or another agent posts a message with `"to": "<your-agent-name>"`,
chatroom-mcp POSTs to your registered `callback_url`:

```json
{
  "event": "chatroom_message",
  "message": { "id": "msg-0042", "from": "human",
               "to": "workbuddy", "type": "finding",
               "subject": "@workbuddy please run IC test",
               "body": "...", "timestamp": "..." }
}
```

Implement a tiny HTTP server to receive these and call `chatroom_pull` for the
full message. Or just poll every 2 seconds (the TUI does this by default).

## 6. Storage compatibility

`chatroom-mcp` reads/writes the **same `messages.jsonl`** that your existing
`workbuddy-agent-comms` v2.1 setup uses. So you can run it side-by-side:

```bash
# Existing workflow
python .workbuddy/comms/send.py --from workbuddy --file draft.json

# New: human-driven TUI
python -m chatroom --comms-dir /path/to/.workbuddy/comms
```

Both produce v2.1-compatible messages.
