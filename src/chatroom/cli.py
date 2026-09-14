"""CLI: `python -m chatroom --comms-dir <path> --port 7777`."""
from __future__ import annotations
import argparse
import sys
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
    # P1 stub: just echo args
    print(f"[chatroom] comms-dir: {a.comms_dir}")
    print(f"[chatroom] port:      {a.port}")
    print(f"[chatroom] no-tui:    {a.no_tui}")
    print("[chatroom] (P1 stub) full impl lands in P2/P3")
    return 0


if __name__ == "__main__":
    sys.exit(main())
