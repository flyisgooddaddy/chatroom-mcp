"""Integration tests for the MCP server (FastMCP 1.x)."""
import pytest
from pathlib import Path

from chatroom.server import create_server


def _unwrap(result):
    sc = result[1] if isinstance(result, tuple) and len(result) == 2 else result
    if isinstance(sc, dict) and 'result' in sc and isinstance(sc['result'], list):
        return sc['result']
    return sc

@pytest.mark.asyncio
async def test_tools_and_resources_registered(tmp_path):
    srv = create_server(tmp_path)
    tools = await srv.list_tools()
    names = {t.name for t in tools}
    assert names == {"chatroom_handshake", "chatroom_pull", "chatroom_post",
                     "chatroom_history", "chatroom_sessions"}
    resources = await srv.list_resources()
    assert any(str(r.uri) == "chat://messages" for r in resources)


@pytest.mark.asyncio
async def test_handshake_then_post_then_pull(tmp_path):
    srv = create_server(tmp_path)
    d1 = _unwrap(await srv.call_tool("chatroom_handshake", {"name": "workbuddy"}))
    assert d1["name"] == "workbuddy"

    msg = {"from": "workbuddy", "type": "finding",
           "timestamp": "2026-09-14T15:00:00+08:00", "subject": "hello world"}
    posted = _unwrap(await srv.call_tool("chatroom_post", {"msg": msg}))
    assert posted["id"].startswith("msg-")
    assert posted["subject"] == "hello world"

    pulled = _unwrap(await srv.call_tool("chatroom_pull", {"limit": 10}))
    assert isinstance(pulled, list)
    assert len(pulled) == 1
    assert pulled[0]["subject"] == "hello world"


@pytest.mark.asyncio
async def test_pull_since_filters(tmp_path):
    srv = create_server(tmp_path)
    for i in range(3):
        await srv.call_tool("chatroom_post", {"msg": {
            "from": "h", "type": "finding",
            "timestamp": f"t{i}", "subject": f"m{i}",
        }})
    all_msgs = _unwrap(await srv.call_tool("chatroom_pull", {"limit": 10}))
    assert len(all_msgs) == 3
    second_id = all_msgs[1]["id"]
    newer = _unwrap(await srv.call_tool("chatroom_pull", {"since": second_id, "limit": 10}))
    assert len(newer) == 1


@pytest.mark.asyncio
async def test_post_rejects_invalid(tmp_path):
    srv = create_server(tmp_path)
    r = _unwrap(await srv.call_tool("chatroom_post", {"msg": {"from": "x", "type": "spam", "subject": "y"}}))
    assert "error" in r


@pytest.mark.asyncio
async def test_sessions_list(tmp_path):
    srv = create_server(tmp_path)
    await srv.call_tool("chatroom_handshake", {"name": "a"})
    await srv.call_tool("chatroom_handshake", {"name": "b"})
    sessions = _unwrap(await srv.call_tool("chatroom_sessions", {}))
    assert {s["name"] for s in sessions} == {"a", "b"}


@pytest.mark.asyncio
async def test_history_limit(tmp_path):
    srv = create_server(tmp_path)
    for i in range(5):
        await srv.call_tool("chatroom_post", {"msg": {
            "from": "h", "type": "finding",
            "timestamp": f"t{i}", "subject": f"m{i}",
        }})
    msgs = _unwrap(await srv.call_tool("chatroom_history", {"limit": 3}))
    assert len(msgs) == 3


@pytest.mark.asyncio
async def test_post_persists_to_disk(tmp_path):
    import json as _json
    srv = create_server(tmp_path)
    await srv.call_tool("chatroom_post", {"msg": {
        "from": "h", "type": "finding",
        "timestamp": "t", "subject": "persist me",
    }})
    p = tmp_path / "messages.jsonl"
    assert p.exists()
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    obj = _json.loads(lines[0])
    assert obj["subject"] == "persist me"
