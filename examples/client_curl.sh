#!/usr/bin/env bash
# Example: connect a curl-based agent to chatroom-mcp.
# Assumes chatroom is running on http://127.0.0.1:7777.
set -euo pipefail
URL="http://127.0.0.1:7777"
NAME="${1:-demo_agent}"

echo "[1] handshake"
curl -sX POST "$URL/mcp" -H "Content-Type: application/json" -d '{
  "jsonrpc": "2.0", "id": 1, "method": "tools/call",
  "params": {"name": "chatroom_handshake", "arguments": {"name": "'"$NAME"'"}}
}'
echo

echo "[2] pull"
curl -sX POST "$URL/mcp" -H "Content-Type: application/json" -d '{
  "jsonrpc": "2.0", "id": 2, "method": "tools/call",
  "params": {"name": "chatroom_pull", "arguments": {"limit": 5}}
}'
echo
