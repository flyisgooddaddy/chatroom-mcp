"""Message protocol. Compatible with workbuddy-agent-comms v2.1.

We do NOT redefine the canonical schema (see `.workbuddy/comms/protocol.json`);
we only validate that a message satisfies the minimum v2.1 contract.
"""

from __future__ import annotations

from typing import Any

REQUIRED_FIELDS = ("id", "from", "type", "timestamp", "subject")
VALID_TYPES = frozenset(
    {"finding", "plan", "request", "ack", "requestion", "done", "blocked", "busy"}
)
# Types that require a brief (开工前契约 per COLLABORATION §3 A1)
TYPES_NEED_BRIEF = frozenset({"plan", "request", "done"})
# Types that require an in_reply_to (回合性回复 per COLLABORATION §3 A2)
TYPES_NEED_REPLY = frozenset({"done", "ack", "requestion"})
# Notify types — informational, no brief, no reply required (per COLLABORATION §10
# 接入器中断协议: B 忙时回 caller "B 忙"). 既不在 NEED_BRIEF 也不在 NEED_REPLY.
TYPES_NOTIFY = frozenset({"busy"})


def is_well_formed(msg: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(msg, dict):
        return False, "message must be a dict"
    for f in REQUIRED_FIELDS:
        if f not in msg:
            return False, f"missing required field: {f!r}"
    if msg["type"] not in VALID_TYPES:
        return False, f"invalid type: {msg['type']!r}"
    if msg["type"] in TYPES_NEED_BRIEF and not msg.get("brief"):
        return False, f"type={msg['type']!r} requires a brief"
    if msg["type"] in TYPES_NEED_REPLY and not msg.get("in_reply_to"):
        return False, f"type={msg['type']!r} requires in_reply_to"
    # Notify types: if either brief or in_reply_to is present, log warning
    # but do NOT reject (informational messages may carry context).
    return True, ""
