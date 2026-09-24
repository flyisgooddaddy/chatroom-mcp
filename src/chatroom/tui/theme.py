"""Colour themes (dark / light) for the TUI."""

from __future__ import annotations

AGENT_COLORS_DARK: dict[str, str] = {
    "workbuddy": "cyan",
    "opencode": "magenta",
    "human": "green",
}

AGENT_COLORS_LIGHT: dict[str, str] = {
    "workbuddy": "dark_cyan",
    "opencode": "dark_magenta",
    "human": "green4",
}

AGENT_COLORS: dict[str, str] = AGENT_COLORS_DARK

DEFAULT_COLOR = "white"

# Map agent names to the closest colour available in both palettes.
PALETTES = {
    "dark": {"workbuddy": "cyan", "opencode": "magenta", "human": "green"},
    "light": {"workbuddy": "dark_cyan", "opencode": "dark_magenta", "human": "green4"},
}


def colors_for(theme: str) -> dict[str, str]:
    return PALETTES.get(theme, AGENT_COLORS_DARK)


def default_color_for(theme: str) -> str:
    return "black" if theme == "light" else "white"
