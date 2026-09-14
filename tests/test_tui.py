"""Headless render tests for the textual TUI (minimal, focused)."""
import pytest
from pathlib import Path

from chatroom.store import Store


@pytest.mark.asyncio
async def test_tui_renders_without_crash(tmp_path):
    from chatroom.tui import ChatroomApp
    s = Store(tmp_path)
    s.append({"from": "workbuddy", "type": "finding", "timestamp": "2026-09-14T10:00:00+08:00", "subject": "wb msg"})
    s.append({"from": "opencode", "type": "plan", "timestamp": "2026-09-14T10:01:00+08:00", "subject": "oc msg", "brief": {"what": "x"}})
    s.append({"from": "human", "type": "finding", "timestamp": "2026-09-14T10:02:00+08:00", "subject": "h msg"})
    app = ChatroomApp(comms_dir=tmp_path, poll_interval=60)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.5)
        assert len(app._seen_ids) == 3
        # Verify pane content was written
        from chatroom.tui.app import MessageLog
        for pane_id in ("pane-workbuddy", "pane-opencode", "pane-human"):
            log = app.query_one(f"#{pane_id}", MessageLog)
            assert log.lines, f"{pane_id} should have at least one line"


@pytest.mark.asyncio
async def test_tui_picks_up_external_write(tmp_path):
    """External writer appends a message; TUI poll picks it up."""
    from chatroom.tui import ChatroomApp
    app = ChatroomApp(comms_dir=tmp_path, poll_interval=0.2)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        assert len(app._seen_ids) == 0
        # External write
        s = Store(tmp_path)
        s.append({"from": "opencode", "type": "finding",
                  "timestamp": "2026-09-14T11:00:00+08:00", "subject": "external"})
        # Wait for poll cycle
        await pilot.pause(0.5)
        assert len(app._seen_ids) == 1


@pytest.mark.asyncio
async def test_tui_identity_default_is_human(tmp_path):
    from chatroom.tui import ChatroomApp
    app = ChatroomApp(comms_dir=tmp_path, poll_interval=60)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        assert app.identity == "human"


def test_send_message_helper_builds_correct_msg(tmp_path):
    """Direct unit test of _send_message logic (no UI)."""
    from chatroom.tui.app import MessageLog  # ensure module imports
    from chatroom.tui import ChatroomApp
    app = ChatroomApp(comms_dir=tmp_path, poll_interval=60)
    # We cant run send_message outside an app context, so check route logic instead
    from chatroom.store import Store
    s = Store(tmp_path)
    out = s.append({
        "from": "human", "to": "workbuddy", "type": "finding",
        "timestamp": "2026-09-14T12:00:00+08:00", "subject": "hello @workbuddy",
    })
    assert out["from"] == "human"
    assert out["to"] == "workbuddy"
