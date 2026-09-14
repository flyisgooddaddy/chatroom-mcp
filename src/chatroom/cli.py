"""CLI: `python -m chatroom --comms-dir <path> --port 7777`.

By default starts both the MCP server (in a background thread) AND the TUI.
Use --no-tui or --no-server to run only one of them.
"""
from __future__ import annotations
import argparse
import asyncio
import sys
import threading
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(prog="chatroom", description="TUI chatroom for multi-agent collab over MCP.")
    p.add_argument("--comms-dir", type=Path, required=True, help="dir with messages.jsonl (e.g. .workbuddy/comms)")
    p.add_argument("--port", type=int, default=7777)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--no-tui", action="store_true", help="run only the MCP server")
    p.add_argument("--no-server", action="store_true", help="run only the TUI (server already up)")
    p.add_argument("--poll-interval", type=float, default=2.0, help="TUI poll interval (s)")
    a = p.parse_args()

    if not a.comms_dir.exists():
        print(f"error: comms-dir does not exist: {a.comms_dir}", file=sys.stderr)
        return 2

    if a.no_tui:
        from chatroom.server import main as server_main
        # Patch sys.argv so server sees the right args
        old = sys.argv
        sys.argv = ["chatroom.server", "--comms-dir", str(a.comms_dir),
                    "--host", a.host, "--port", str(a.port)]
        try:
            server_main()
        finally:
            sys.argv = old
        return 0

    if a.no_server:
        from chatroom.tui import ChatroomApp
        app = ChatroomApp(comms_dir=a.comms_dir, server_url=None, poll_interval=a.poll_interval)
        app.run()
        return 0

    # Default: run server in background thread + TUI in main thread
    from chatroom.server import create_server
    from chatroom.tui import ChatroomApp

    server = create_server(a.comms_dir)
    server.settings.host = a.host
    server.settings.port = a.port

    def run_server():
        server.run(transport="streamable-http")

    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    print(f"[chatroom] server starting on http://{a.host}:{a.port}")
    print(f"[chatroom] comms-dir: {a.comms_dir}")
    print(f"[chatroom] TUI launching...")
    # Give uvicorn a moment to bind
    import time
    time.sleep(0.5)

    app = ChatroomApp(
        comms_dir=a.comms_dir,
        server_url=f"http://{a.host}:{a.port}",
        poll_interval=a.poll_interval,
    )
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
