"""openwriter_agent: openwriter agent that forwards human messages to a queue file.

When someone @openwriter's, this agent writes the incoming message to
`_inbox.jsonl` (for the operator/LLM to pick up) and ACKs in the chatroom.

The operator (e.g. an LLM session) reads _inbox.jsonl, writes replies to
_outbox.jsonl, and this agent posts them into the chatroom.

Usage:
    python -m scripts.openwriter_agent --comms-dir <path> --port 9000
"""
from __future__ import annotations
import argparse
import asyncio
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from chatroom.server import create_app


class OpenwriterAgent:
    def __init__(self, comms_dir: Path, port: int) -> None:
        self.comms_dir = comms_dir
        self.port = port
        self.callback_url = f"http://127.0.0.1:{port}/notify"
        self.inbox = comms_dir / "_inbox.jsonl"      # human -> openwriter
        self.outbox = comms_dir / "_outbox.jsonl"    # openwriter -> chatroom
        self.app = create_app(comms_dir)
        self.mcp = self.app.state.mcp
        self.seen: set[str] = set()
        self.seen_lock = asyncio.Lock()
        self.sent_outbox: set[str] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    async def handshake(self) -> None:
        await self.mcp.call_tool(
            "chatroom_handshake",
            {"name": "openwriter", "callback_url": self.callback_url},
        )
        print(f"[openwriter] registered, callback={self.callback_url}")

    async def post_to_chatroom(self, sender: str, subject: str, body: str) -> None:
        msg = {
            "from": "openwriter", "type": "finding", "to": sender,
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "subject": "re: " + subject[:80],
            "body": body,
        }
        r = await self.mcp.call_tool("chatroom_post", {"msg": msg})
        posted = r[1] if isinstance(r, tuple) else r
        print(f"[openwriter] posted -> {posted.get('id')}")

    async def handle_incoming(self, msg: dict[str, Any]) -> None:
        mid = msg.get("id", "")
        if not mid:
            return
        # Atomic check-and-claim: only ONE caller proceeds per message id.
        async with self.seen_lock:
            if mid in self.seen:
                print(f"[openwriter] SKIP {mid} (already seen)")
                return
            self.seen.add(mid)
        print(f"[openwriter] NEW {mid} (seen size now {len(self.seen)})")
        if msg.get("from") == "openwriter":
            return
        sender = msg.get("from", "?")
        subj = msg.get("subject", "")
        body = msg.get("body", "")
        targeted = (msg.get("to") == "openwriter") or ("@openwriter" in (subj + " " + body).lower())
        if not targeted:
            return
        record = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "incoming_id": mid,
            "from": sender,
            "subject": subj,
            "body": body,
        }
        # Dedup by incoming_id: skip if this id already in _inbox.jsonl
        if self.inbox.exists():
            try:
                with self.inbox.open(encoding="utf-8") as f:
                    for line in f:
                        try:
                            if json.loads(line.strip()).get("incoming_id") == mid:
                                print(f"[openwriter] inbox dedup: {mid} already in queue, skipping")
                                return
                        except Exception:
                            continue
            except Exception:
                pass
        with self.inbox.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[openwriter] queued to inbox: {mid} from {sender} (no auto-reply)")

    # --- push receiver ---
    def start_callback_server(self) -> None:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self_h):
                n = int(self_h.headers.get("Content-Length", 0))
                raw = self_h.rfile.read(n).decode("utf-8")
                try:
                    payload = json.loads(raw)
                    msg = payload.get("message", {})
                    print(f"[openwriter] PUSH: {msg.get('id')} {msg.get('subject','')[:40]}")
                    if bridge.loop is not None:
                        asyncio.run_coroutine_threadsafe(
                            bridge.handle_incoming(msg), bridge.loop
                        )
                except Exception as e:
                    print(f"[openwriter] push parse error: {e}")
                self_h.send_response(200)
                self_h.send_header("Content-Type", "application/json")
                self_h.end_headers()
                self_h.wfile.write(b'{"ok":true}')

            def log_message(self_h, *a):
                pass

        srv = HTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"[openwriter] push receiver on {self.callback_url}")

    # --- watch outbox for replies written by the operator ---
    async def outbox_loop(self) -> None:
        while True:
            if self.outbox.exists():
                for line in self.outbox.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = rec.get("key") or rec.get("incoming_id") or line
                    if key in self.sent_outbox:
                        continue
                    self.sent_outbox.add(key)
                    await self.post_to_chatroom(
                        rec.get("to", "human"),
                        rec.get("subject", "reply"),
                        rec.get("body", ""),
                    )
            await asyncio.sleep(2)

    async def poll_loop(self) -> None:
        while True:
            try:
                r = await self.mcp.call_tool("chatroom_pull", {"limit": 50})
                raw = r[1] if isinstance(r, tuple) else r
                msgs = raw.get("messages", []) if isinstance(raw, dict) else raw
                for m in msgs:
                    await self.handle_incoming(m)
            except Exception as e:
                print(f"[openwriter] poll error: {e}")
            await asyncio.sleep(3)

    async def preload_seen(self) -> None:
        """Mark all existing messages as already-seen so we only react to NEW ones.

        DISABLED by default: history files can contain 100s of old ids, and the
        reload happens across processes. Inbox writes are naturally idempotent
        (we dedupe by incoming_id in tick). Keep the seen set empty on startup
        and rely on _inbox.jsonl dedup at the operator side.
        """
        # No-op for now. To re-enable, read messages.jsonl here and add ids to self.seen.
        print("[openwriter] preload disabled; relying on inbox dedup")

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        await self.handshake()
        await self.preload_seen()
        self.start_callback_server()
        print("[openwriter] ready (push + poll + outbox watcher) - only NEW messages trigger replies")
        await asyncio.gather(self.poll_loop(), self.outbox_loop())


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--comms-dir", type=Path, required=True)
    p.add_argument("--port", type=int, default=9000)
    a = p.parse_args()
    asyncio.run(OpenwriterAgent(a.comms_dir, a.port).run())


if __name__ == "__main__":
    main()
