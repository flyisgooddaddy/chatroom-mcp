"""chatroom-mcp client SDK: register an agent, poll for @-mentions, inject prompts.

This is the Python equivalent of the chatroom-bridge OpenCode plugin: it
runs in any agent process, registers itself with a chatroom server, polls
for messages targeting the agent, and hands them to a framework-specific
driver for prompt injection.

Public surface:
    AgentConfig       -- load chatroom.yaml
    AgentClient       -- handshake + heartbeat + poll + claim + inject
    ChatroomTransport -- low-level MCP JSON-RPC client (httpx-based)

Usage:
    config = AgentConfig.from_yaml("chatroom.yaml")
    transport = ChatroomTransport(config.server_url)
    client = AgentClient(transport, config)
    await client.start()         # handshake + start heartbeat + poll
    await client.run_forever()   # block until SIGINT
"""

from __future__ import annotations

__version__ = "0.1.0"
