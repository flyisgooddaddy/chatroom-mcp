"""Textual app: three-pane chatroom + input box.

Layout (top to bottom):
  - Header: connection status + active identity (workbuddy | opencode | human)
  - Three vertical panes (workbuddy | opencode | human)
  - Input box (Tab cycles target identity)
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, RichLog, Static

from chatroom.store import Store
from chatroom.tui.theme import AGENT_COLORS, DEFAULT_COLOR


def _fmt_ts(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return "??:??"


def _color_for(sender: str) -> str:
    return AGENT_COLORS.get(sender, DEFAULT_COLOR)


class MessageLog(RichLog):
    """A scrollable log for one agent (or human)."""

    def append_message(self, msg: dict[str, Any]) -> None:
        sender = str(msg.get("from", "?"))
        ts = _fmt_ts(str(msg.get("timestamp", "")))
        subj = str(msg.get("subject", ""))
        typ = str(msg.get("type", ""))
        msg_id = str(msg.get("id", ""))
        color = _color_for(sender)
        line = f"[dim]{ts}[/dim] [dim {color}]{sender}[/dim {color}] [bold {color}]{typ}[/bold {color}] {msg_id} [dim]|[/dim] {subj}"
        self.write(line)


class ChatroomApp(App):
    """Three-pane chatroom TUI."""

    CSS = """
    Screen { layout: vertical; }
    #header-row { height: 1; background: $boost; padding: 0 1; }
    #panes { height: 1fr; }
    MessageLog { border: solid $accent; width: 1fr; }
    #pane-human { border: solid green; }
    #pane-workbuddy { border: solid cyan; }
    #pane-opencode { border: solid magenta; }
    #status-bar { height: 1; background: $boost; padding: 0 1; color: $text-muted; }
    #input { height: 3; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("tab", "cycle_identity", "Switch identity"),
        Binding("ctrl+r", "refresh", "Refresh"),
    ]

    identity: reactive[str] = reactive("human")
    identities: tuple[str, ...] = ("workbuddy", "opencode", "human")

    def __init__(self, comms_dir: Path, server_url: str | None = None,
                 poll_interval: float = 2.0) -> None:
        super().__init__()
        self.comms_dir = Path(comms_dir)
        self.store = Store(self.comms_dir)
        self.server_url = server_url
        self.poll_interval = poll_interval
        self._seen_ids: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Static(id="status-bar")
        with Horizontal(id="panes"):
            yield MessageLog(id="pane-workbuddy", highlight=True, markup=True, wrap=True)
            yield MessageLog(id="pane-opencode", highlight=True, markup=True, wrap=True)
            yield MessageLog(id="pane-human", highlight=True, markup=True, wrap=True)
        yield Input(placeholder="@workbuddy ... | Tab to switch | Ctrl+Q to quit", id="input")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_status()
        self._initial_load()
        self.set_interval(self.poll_interval, self._poll_new)
        self.query_one("#input", Input).focus()

    def _initial_load(self) -> None:
        for m in self.store.read_all():
            self._route(m)
            self._seen_ids.add(str(m.get("id", "")))

    def _poll_new(self) -> None:
        new_count = 0
        for m in self.store.read_all():
            mid = str(m.get("id", ""))
            if mid in self._seen_ids:
                continue
            self._route(m)
            self._seen_ids.add(mid)
            new_count += 1
        if new_count:
            self.refresh_status(f"+{new_count} new")

    def _route(self, msg: dict[str, Any]) -> None:
        sender = str(msg.get("from", ""))
        pane_id = {"workbuddy": "pane-workbuddy",
                   "opencode": "pane-opencode"}.get(sender, "pane-human")
        try:
            self.query_one(f"#{pane_id}", MessageLog).append_message(msg)
        except Exception:
            pass

    def refresh_status(self, suffix: str = "") -> None:
        bar = self.query_one("#status-bar", Static)
        n = len(self._seen_ids)
        identity_hint = f"-> @{self.identity}" if self.identity != "human" else "-> all"
        bar.update(f"[bold]chatroom-mcp[/bold]  msgs: {n}  identity: [{self.identity}] {identity_hint}  {suffix}")

    def action_cycle_identity(self) -> None:
        i = self.identities.index(self.identity)
        self.identity = self.identities[(i + 1) % len(self.identities)]
        self.refresh_status()

    def action_refresh(self) -> None:
        self._poll_new()
        self.refresh_status("refreshed")

    def on_chat_input_submitted(self, event) -> None:
        # textual v8 fires on any Input event; only handle Submitted
        if not hasattr(event, "value"):
            return
        text = event.value.strip()
        if not text:
            return
        self.query_one("#input", Input).value = ""
        self._send_message(text)
        self.refresh_status()

    def _send_message(self, text: str) -> None:
        # Parse @target prefix
        target = self.identity if self.identity != "human" else "all"
        if text.startswith("@"):
            parts = text.split(maxsplit=1)
            tag = parts[0][1:].strip()
            body = parts[1] if len(parts) > 1 else ""
            if tag in self.identities:
                target = tag
                text = body
        # Build msg (schema-compatible finding)
        msg: dict[str, Any] = {
            "from": "human",
            "to": target if target != "all" else None,
            "type": "finding",
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "subject": text[:140] or "(empty)",
            "body": text,
        }
        if target != "all":
            msg["to"] = target
        else:
            msg.pop("to", None)
        try:
            stored = self.store.append(msg)
            self._seen_ids.add(stored["id"])
            self._route(stored)
        except Exception as e:
            self.refresh_status(f"error: {e}")
