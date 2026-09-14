"""Textual app (P1 stub: placeholder). Real impl in P3."""
from __future__ import annotations
from textual.app import App, ComposeResult
from textual.widgets import Static


class ChatroomApp(App):
    """Three-pane textual chatroom."""

    CSS = """
    Screen { layout: vertical; }
    #placeholder { content-align: center middle; height: 100%; }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold]chatroom-mcp[/bold] (P1 stub)\n\n"
            "Real TUI implementation lands in P3.\n"
            "See plan/phase 3 in docs/ROADMAP.md.",
            id="placeholder",
        )
