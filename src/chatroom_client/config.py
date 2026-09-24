"""Load chatroom.yaml into AgentConfig.

Schema (all keys optional except agent.name and server.url):
    agent:
      name: my-agent           # required, used for chatroom_handshake
      aliases: [me, my-alias]  # extra names isTargeting() should match
      callback_path: /chatroom/cb  # path on local callback server
    server:
      url: http://127.0.0.1:7777   # required
      room: main
    driver: opencode          # one of: opencode, workbuddy, none
    state_dir: ~/.local/share/chatroom-client   # locks + markers
    heartbeat_seconds: 30
    poll_timeout_seconds: 25
    session_marker_filename: chatroom-client.active
    lastinject_filename: chatroom-client.lastinject
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]
except ImportError as e:  # pragma: no cover - import guard
    raise SystemExit(
        "chatroom_client requires PyYAML. Install with: pip install chatroom-mcp[client]"
    ) from e


@dataclass(slots=True)
class ServerConfig:
    url: str = "http://127.0.0.1:7777"
    room: str = "main"


@dataclass(slots=True)
class AgentSection:
    name: str = ""
    aliases: list[str] = field(default_factory=list)
    callback_path: str = "/chatroom/cb"


@dataclass(slots=True)
class AgentConfig:
    agent: AgentSection
    server: ServerConfig
    driver: str = "none"
    state_dir: Path = field(
        default_factory=lambda: Path.home() / ".local" / "share" / "chatroom-client"
    )
    heartbeat_seconds: float = 30.0
    poll_timeout_seconds: float = 25.0
    session_marker_filename: str = "chatroom-client.active"
    lastinject_filename: str = "chatroom-client.lastinject"

    @property
    def all_names(self) -> list[str]:
        """Names isTargeting() matches (main name + aliases)."""
        return [self.agent.name, *self.agent.aliases]

    @property
    def session_marker_path(self) -> Path:
        return self.state_dir / self.session_marker_filename

    @property
    def lastinject_path(self) -> Path:
        return self.state_dir / self.lastinject_filename

    @property
    def claims_dir(self) -> Path:
        return self.state_dir / "chatroom-client.claims.d"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentConfig:
        a = d.get("agent") or {}
        s = d.get("server") or {}
        agent = AgentSection(
            name=a.get("name", ""),
            aliases=list(a.get("aliases", []) or []),
            callback_path=a.get("callback_path", "/chatroom/cb"),
        )
        server = ServerConfig(url=s.get("url", "http://127.0.0.1:7777"), room=s.get("room", "main"))
        if not agent.name:
            raise ValueError("chatroom.yaml: agent.name is required")
        if not server.url:
            raise ValueError("chatroom.yaml: server.url is required")
        state_dir_raw = d.get("state_dir")
        state_dir = (
            Path(state_dir_raw).expanduser()
            if state_dir_raw
            else Path.home() / ".local" / "share" / "chatroom-client"
        )
        return cls(
            agent=agent,
            server=server,
            driver=d.get("driver", "none"),
            state_dir=state_dir,
            heartbeat_seconds=float(d.get("heartbeat_seconds", 30.0)),
            poll_timeout_seconds=float(d.get("poll_timeout_seconds", 25.0)),
            session_marker_filename=d.get("session_marker_filename", "chatroom-client.active"),
            lastinject_filename=d.get("lastinject_filename", "chatroom-client.lastinject"),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> AgentConfig:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"chatroom.yaml not found: {p}")
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(
                f"chatroom.yaml: top-level must be a mapping, got {type(data).__name__}"
            )
        return cls.from_dict(data)

    @classmethod
    def from_yaml_or_default(cls, path: str | Path | None) -> AgentConfig:
        """Load from path if it exists, else build a default from env / cwd."""
        if path and Path(path).exists():
            return cls.from_yaml(path)
        return cls.from_env()

    @classmethod
    def from_env(cls) -> AgentConfig:
        import os

        name = os.environ.get("CHATROOM_AGENT_NAME", "")
        url = os.environ.get("CHATROOM_HTTP", "http://127.0.0.1:7777")
        room = os.environ.get("CHATROOM_ROOM", "main")
        if not name:
            raise ValueError(
                "no chatroom.yaml found and CHATROOM_AGENT_NAME env not set; "
                "either create chatroom.yaml or set CHATROOM_AGENT_NAME"
            )
        return cls.from_dict({"agent": {"name": name}, "server": {"url": url, "room": room}})
