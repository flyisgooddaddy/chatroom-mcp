"""Tiny MCP streamable-HTTP client for verifying chatroom-mcp from another process."""
import json
import httpx


def rpc(url, method, params=None, session_id=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    r = httpx.post(url, json=body, headers=headers, timeout=10)
    sid = r.headers.get("mcp-session-id") or session_id
    text = r.text
    if text.startswith("event:"):
        for line in text.splitlines():
            if line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
                return data, sid
    return (json.loads(text) if text.strip() else None), sid


def main():
    url = "http://127.0.0.1:7777/mcp"

    print("=== 1. initialize ===")
    data, sid = rpc(url, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "tiny-mcp-client", "version": "0.0.1"},
    })
    print("session_id:", sid)
    print("server:", data["result"]["serverInfo"])
    print()

    httpx.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
               headers={"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "mcp-session-id": sid}, timeout=5)
    print()

    print("=== 2. tools/list ===")
    data, _ = rpc(url, "tools/list", session_id=sid)
    for t in data["result"]["tools"]:
        print("  " + t["name"] + ": " + t.get("description","")[:80])
    print()

    print("=== 3. handshake ===")
    data, _ = rpc(url, "tools/call", {
        "name": "chatroom_handshake",
        "arguments": {"name": "bobo", "callback_url": "http://127.0.0.1:9999/hook"},
    }, session_id=sid)
    print("result:", json.dumps(data["result"], indent=2)[:300])
    print()

    print("=== 4. post a finding ===")
    data, _ = rpc(url, "tools/call", {
        "name": "chatroom_post",
        "arguments": {"msg": {
            "from": "bobo", "type": "finding",
            "timestamp": "2026-09-14T18:06:00+08:00",
            "subject": "hello from bobo via tiny MCP client",
            "body": "External agent connects, posts via MCP, persisted to disk.",
        }},
    }, session_id=sid)
    posted = data["result"]
    print("posted id: " + str(posted.get("id")) + " subject: " + str(posted.get("subject")))
    print()

    print("=== 5. pull ===")
    data, _ = rpc(url, "tools/call", {
        "name": "chatroom_pull", "arguments": {"limit": 5},
    }, session_id=sid)
    msgs = data["result"]
    print("pulled " + str(len(msgs)) + " msgs, latest:")
    for m in msgs[-3:]:
        print("  [" + str(m["from"]) + "] " + str(m.get("subject",""))[:80])
    print()

    print("=== 6. sessions ===")
    data, _ = rpc(url, "tools/call", {
        "name": "chatroom_sessions", "arguments": {},
    }, session_id=sid)
    sessions = data["result"]
    print("sessions: " + str([s["name"] for s in sessions]))
    print()
    print("=== ALL OK ===")


if __name__ == "__main__":
    main()
