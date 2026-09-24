"""chatroom-client CLI: register / status / version.

Usage:
    chatroom-client register [-c chatroom.yaml]
    chatroom-client status   [-c chatroom.yaml]
    chatroom-client --version
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from . import __version__
from .config import AgentConfig
from .core import AgentClient, AgentState, ChatroomTransport


def _setup_logging(state_dir: Path, *, verbose: bool) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    log_file = state_dir / "chatroom-client.log"
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)


def _cmd_register(args: argparse.Namespace) -> int:
    try:
        cfg = AgentConfig.from_yaml_or_default(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"chatroom-client: {e}", file=sys.stderr)
        return 2
    _setup_logging(cfg.state_dir, verbose=args.verbose)
    log = logging.getLogger("chatroom_client.cli")
    log.info("starting agent=%s server=%s", cfg.agent.name, cfg.server.url)

    async def run() -> None:
        transport = ChatroomTransport(cfg.server.url)
        client = AgentClient(transport, cfg)
        loop = asyncio.get_running_loop()

        def _on_signal(signame: str) -> None:
            log.info("received %s, shutting down", signame)
            loop.create_task(client.stop())

        for sig_name in ("SIGINT", "SIGTERM"):
            try:
                loop.add_signal_handler(
                    getattr(signal, sig_name),
                    lambda s=sig_name: _on_signal(s),
                )
            except NotImplementedError:
                # Windows + ProactorEventLoop doesn't support add_signal_handler
                pass
        try:
            await client.run_forever()
        finally:
            log.info("agent client stopped")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        cfg = AgentConfig.from_yaml_or_default(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"chatroom-client: {e}", file=sys.stderr)
        return 2
    state = AgentState(cfg.session_marker_path, cfg.lastinject_path)
    active = state.read_active()
    print(f"agent        : {cfg.agent.name}")
    print(f"server       : {cfg.server.url}")
    print(f"room         : {cfg.server.room}")
    print(f"state_dir    : {cfg.state_dir}")
    print(f"active_marker: {active or '(none)'}")
    try:
        lastinject_raw = cfg.lastinject_path.read_text(encoding="utf-8").strip()
        print(f"last_inject  : {lastinject_raw}")
    except FileNotFoundError:
        print("last_inject  : (none)")
    claim_dir = cfg.claims_dir
    try:
        n = sum(1 for _ in claim_dir.iterdir() if _.is_file())
    except FileNotFoundError:
        n = 0
    print(f"claims_held  : {n}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chatroom-client",
        description="Register an agent with a chatroom server and forward @-mentions.",
    )
    p.add_argument("--version", action="version", version=f"chatroom-client {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_reg = sub.add_parser("register", help="handshake + start heartbeat/poll loop")
    p_reg.add_argument(
        "-c", "--config", type=Path, default=None, help="path to chatroom.yaml (else env)"
    )
    p_reg.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p_reg.set_defaults(func=_cmd_register)

    p_st = sub.add_parser("status", help="show marker / claims / config")
    p_st.add_argument("-c", "--config", type=Path, default=None, help="path to chatroom.yaml")
    p_st.set_defaults(func=_cmd_status)
    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
