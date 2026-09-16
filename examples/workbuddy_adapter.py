r"""workbuddy <-> chatroom thin adapter.

Polls chatroom-mcp; when someone @'s `workbuddy`, wakes the active workbuddy
session via `codebuddy --resume` and posts the model's reply back.

Why this exists
---------------
WorkBuddy has no inbound HTTP push endpoint, so chatroom's @-push (which POSTs
to a `callback_url`) cannot reach it directly. The chatroom `chatroom_handshake`
tool now accepts an empty `callback_url` for pull-only agents; this adapter
plays that role:

    human @workbuddy  ->  chatroom stores message  <-  adapter polls chatroom_pull
    adapter runs      ->  codebuddy --resume <sessionId> -p "<prompt>"
    adapter posts     ->  chatroom_post (type=ack, in_reply_to=<id>)

The critical unlock: the codebuddy CLI must be pointed at WorkBuddy's data
directory via `CODEBUDDY_CONFIG_DIR=<.workbuddy>`, otherwise it defaults to
CodeBuddy's `~/.codebuddy` and reports "No conversation found".

Run
---
    D:\Python\python.exe examples/workbuddy_adapter.py

Or set the WB_* env vars (see below) and run.

Env / defaults
--------------
All have sensible defaults for this machine; override via env or edit.
    CHATROOM_URL              chatroom MCP endpoint
    WB_AGENT_NAME             name registered in chatroom (default: workbuddy)
    WB_SESSION_ID             workbuddy/CodeBuddy session id to resume into
    WB_NODE_EXE               node.exe bundled with WorkBuddy
    WB_CODEBUDDY_JS           dist/codebuddy.js (full bundle, not headless)
    WB_CODEBUDDY_CONFIG_DIR   ~/.workbuddy (so CLI uses WorkBuddy's store)
    WB_POLL_INTERVAL          seconds between pulls
    WB_WAKE_TIMEOUT           wake subprocess timeout
    WB_PROCESSED_FILE         path to the persisted processed-msg-id set
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

CHATROOM_URL = os.environ.get("CHATROOM_URL", "http://127.0.0.1:7777/mcp")
AGENT_NAME = os.environ.get("WB_AGENT_NAME", "workbuddy")
SESSION_ID = os.environ.get(
    "WB_SESSION_ID", "0d830c44-eb35-4c4a-8b16-0d33ceb6c0dd"
)
NODE_EXE = os.environ.get(
    "WB_NODE_EXE",
    r"C:/Users/波波/.workbuddy/binaries/node/versions/22.22.2-3/node.exe",
)
CODEBUDDY_JS = os.environ.get(
    "WB_CODEBUDDY_JS",
    r"D:/worknbuddy/WorkBuddy/resources/app.asar.unpacked/cli/dist/codebuddy.js",
)
CODEBUDDY_CONFIG_DIR = os.environ.get(
    "WB_CODEBUDDY_CONFIG_DIR", r"C:/Users/波波/.workbuddy"
)
POLL_INTERVAL = float(os.environ.get("WB_POLL_INTERVAL", "3.0"))
WAKECMD_TIMEOUT = int(os.environ.get("WB_WAKE_TIMEOUT", "180"))
CWD = os.environ.get("WB_CWD", r"C:/Users/波波/Desktop/dev")
PROCESSED_FILE = Path(
    os.environ.get(
        "WB_PROCESSED_FILE",
        str(Path(CODEBUDDY_CONFIG_DIR) / "wb_processed.json"),
    )
)


# ---------------------------------------------------------------------------
# processed-set persistence: survive restarts so we don't double-respond
# ---------------------------------------------------------------------------
def load_processed() -> set[str]:
    try:
        return set(json.loads(PROCESSED_FILE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return set()


def save_processed(s: set[str]) -> None:
    try:
        PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROCESSED_FILE.write_text(
            json.dumps(sorted(s), ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# wake: shell out to codebuddy CLI, capture stdout as bytes, decode utf-8.
# Decoding as bytes (not text=True) avoids the Windows console-codepage bug
# where a non-gbk byte in the child's stdout crashes the reader thread and
# silently drops the output.
# ---------------------------------------------------------------------------
def wake_session(prompt: str) -> str:
    env = {**os.environ, "CODEBUDDY_CONFIG_DIR": CODEBUDDY_CONFIG_DIR, "NO_COLOR": "1"}
    cmd = [
        NODE_EXE, CODEBUDDY_JS,
        "-r", SESSION_ID,
        "-p", prompt,
        "-y",
        "--print",
        "--tools", "",          # text-only reply, no tool use
        "--max-turns", "3",
        # No --fork-session: we resume SESSION_ID directly so the @ message +
        # reply land as turns in 0d830c44 and show up in the codebuddy
        # --serve web UI. Concurrent appends to the .jsonl are atomic, so
        # this is safe as long as no other process holds SESSION_ID (i.e.
        # close the WorkBuddy GUI daemon; the --serve web UI is fine).
    ]
    proc = subprocess.run(
        cmd, capture_output=True, timeout=WAKECMD_TIMEOUT, cwd=CWD, env=env
    )
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    err = proc.stderr.decode("utf-8", errors="replace").strip()
    if not out:
        out = ("(stderr) " + err[-1000:]) if err else f"(wake failed, rc={proc.returncode})"
    elif proc.returncode != 0:
        out += "\n(stderr tail) " + err[-300:]
    return out


def build_prompt(msg: dict) -> str:
    sender = msg.get("from") or "?"
    subj = msg.get("subject") or ""
    body = msg.get("body") or ""
    return f"[chatroom @ from {sender}] {subj}\n{body}".strip()


async def handle_message(s: ClientSession, msg: dict, processed: set[str]) -> None:
    mid = msg.get("id")
    sender = msg.get("from") or "?"
    subj = msg.get("subject") or ""
    processed.add(mid)
    save_processed(processed)
    prompt = build_prompt(msg)
    print(f"[adapter] @wake: {mid} {sender} -> {AGENT_NAME}: {subj}", flush=True)
    try:
        reply = await asyncio.to_thread(wake_session, prompt)
    except subprocess.TimeoutExpired:
        reply = "(wake timed out)"
    except Exception as e:  # noqa: BLE001
        reply = f"(wake error: {e!r})"
    print(f"[adapter] reply ({len(reply)} chars): {reply[:200]!r}", flush=True)
    await s.call_tool(
        "chatroom_post",
        {
            "msg": {
                "from": AGENT_NAME,
                "type": "ack",
                "subject": f"re: {subj}" if subj else "re",
                "body": reply,
                "in_reply_to": mid,
                "to": sender,
            }
        },
    )
    print(f"[adapter] posted ack {mid} -> {sender}", flush=True)


async def main() -> None:
    processed = load_processed()
    print(
        f"[adapter] loaded {len(processed)} processed ids from {PROCESSED_FILE}",
        flush=True,
    )
    async with streamablehttp_client(CHATROOM_URL) as (read, write, *_):
        async with ClientSession(read, write) as s:
            await s.initialize()
            await s.call_tool(
                "chatroom_handshake",
                {
                    "name": AGENT_NAME,
                    "bound_session_id": SESSION_ID,
                    "callback_url": "",  # workbuddy has no inbound push endpoint
                    "session_name": "wb-chatroom-adapter",
                },
            )
            print(
                f"[adapter] registered as {AGENT_NAME} (session={SESSION_ID}); "
                f"poll={POLL_INTERVAL}s wake_timeout={WAKECMD_TIMEOUT}s",
                flush=True,
            )
            while True:
                r = await s.call_tool("chatroom_pull", {"limit": 50})
                msgs = (r.structuredContent or {}).get("messages", [])
                for m in msgs:
                    if m.get("to") != AGENT_NAME or m.get("id") in processed:
                        continue
                    await handle_message(s, m, processed)
                await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)