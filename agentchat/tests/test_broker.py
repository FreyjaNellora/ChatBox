#!/usr/bin/env python3
"""Unit tests for AgentChat broker core."""

import asyncio
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from broker_core import Broker, AuthError, ValidationError, NotFoundError


@pytest.fixture
def broker():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        dispatch_path = Path(tmpdir) / "dispatch_comms.jsonl"
        dispatch_state_path = Path(tmpdir) / "dispatch_state.json"
        b = Broker(db_path=db_path, dispatch_path=dispatch_path, dispatch_state_path=dispatch_state_path)
        b.set_lock(asyncio.Lock())
        yield b


@pytest.fixture
def authed_broker():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        dispatch_path = Path(tmpdir) / "dispatch_comms.jsonl"
        dispatch_state_path = Path(tmpdir) / "dispatch_state.json"
        b = Broker(db_path=db_path, dispatch_path=dispatch_path, dispatch_state_path=dispatch_state_path, auth_token="secret123")
        b.set_lock(asyncio.Lock())
        yield b


@pytest.mark.asyncio
async def test_hello_registers_agent(broker):
    result = await broker.hello("kimi", "3", ["#phase-3"])
    assert result["status"] == "ok"
    assert result["agent"] == "kimi"
    assert result["phase"] == "3"
    assert "#phase-3" in result["subscribed"]


@pytest.mark.asyncio
async def test_chat_message(broker):
    r = await broker.hello("kimi", "3", ["#phase-3"]); tok = r["session_token"]
    result = await broker.chat("kimi", "#phase-3", "Hello from Phase 3", session_token=tok)
    assert result["status"] == "ok"
    assert "message_id" in result


@pytest.mark.asyncio
async def test_start_post(broker):
    r = await broker.hello("kimi", "3", ["#phase-3"]); tok = r["session_token"]
    result = await broker.start_post("kimi", "#change-orders", "CO-023: Parser mismatch", "Phase 3 parser returns Foo, Phase 2 expects Bar", "change-order", tier=2, session_token=tok)
    assert result["status"] == "ok"
    assert "post_id" in result


@pytest.mark.asyncio
async def test_reply_to_post(broker):
    r = await broker.hello("kimi", "3", ["#phase-3"]); tok = r["session_token"]
    post = await broker.start_post("kimi", "#change-orders", "CO-023: Parser mismatch", "Phase 3 parser returns Foo, Phase 2 expects Bar", "change-order", session_token=tok)
    post_id = post["post_id"]

    r = await broker.hello("claude", "2", ["#phase-2"]); tok = r["session_token"]
    result = await broker.reply("claude", post_id, "Looking at it now", session_token=tok)
    assert result["status"] == "ok"
    assert "reply_id" in result


@pytest.mark.asyncio
async def test_get_post_with_replies(broker):
    r = await broker.hello("kimi", "3", ["#phase-3"]); tok = r["session_token"]
    post = await broker.start_post("kimi", "#change-orders", "CO-023: Parser mismatch", "Phase 3 parser returns Foo, Phase 2 expects Bar", "change-order", session_token=tok)
    post_id = post["post_id"]

    await broker.reply("kimi", post_id, "Update: found the root cause", session_token=tok)

    result = await broker.get_post(post_id)
    assert result["status"] == "ok"
    assert result["post"]["title"] == "CO-023: Parser mismatch"
    assert len(result["replies"]) == 1
    assert result["replies"][0]["body"] == "Update: found the root cause"


@pytest.mark.asyncio
async def test_listen_returns_messages(broker):
    r = await broker.hello("kimi", "3", ["#phase-3"]); tok = r["session_token"]
    await broker.chat("kimi", "#phase-3", "Message 1", session_token=tok)

    result = await broker.listen("kimi", ["#phase-3"], "full", 0, 1000, session_token=tok)
    assert result["status"] == "ok"
    assert len(result["messages"]) == 1
    assert result["messages"][0]["body"] == "Message 1"


@pytest.mark.asyncio
async def test_listen_respects_max_msgs(broker):
    r = await broker.hello("kimi", "3", ["#phase-3"]); tok = r["session_token"]
    for i in range(15):
        await broker.chat("kimi", "#phase-3", f"Message {i}", session_token=tok)

    result = await broker.listen("kimi", ["#phase-3"], "full", 0, 1000, max_msgs=5, session_token=tok)
    assert len(result["messages"]) == 5


@pytest.mark.asyncio
async def test_dispatch_mirror(broker):
    r = await broker.hello("kimi", "3", ["#dispatch"]); tok = r["session_token"]
    await broker.start_post("kimi", "#dispatch", "PLAN: Refactor eval cache", "Switch to hash map with LRU eviction", "plan", tier=1, session_token=tok)

    with open(broker.dispatch_path, "r") as f:
        lines = f.readlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["type"] == "plan"
    assert entry["tier"] == 1
    assert entry["phase"] == "3"


@pytest.mark.asyncio
async def test_subscribe_unsubscribe(broker):
    r = await broker.hello("kimi", "3", ["#phase-3"]); tok = r["session_token"]
    result = await broker.subscribe("kimi", "#general", "headlines", session_token=tok)
    assert result["status"] == "ok"
    assert result["view"] == "headlines"

    result = await broker.unsubscribe("kimi", "#general", session_token=tok)
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_rooms_lists_channels(broker):
    result = await broker.rooms()
    assert result["status"] == "ok"
    channel_names = [c["name"] for c in result["channels"]]
    assert "#general" in channel_names
    assert "#dispatch" in channel_names


@pytest.mark.asyncio
async def test_approve_deny_mirror(broker):
    r = await broker.hello("kimi", "3", ["#dispatch"]); tok = r["session_token"]
    r = await broker.hello("nate", "1", ["#dispatch"]); tok2 = r["session_token"]
    post = await broker.start_post("kimi", "#dispatch", "PLAN: Test plan", "Description", "plan", tier=1, session_token=tok)

    result = await broker.approve("nate", post["post_id"], "Looks good", session_token=tok2)
    assert result["status"] == "ok"

    with open(broker.dispatch_path, "r") as f:
        lines = f.readlines()
    assert len(lines) == 2
    approval = json.loads(lines[1])
    assert approval["type"] == "approval"


# --- Auth tests -----------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_required_on_hello(authed_broker):
    with pytest.raises(AuthError):
        await authed_broker.hello("kimi", "3", ["#phase-3"])


@pytest.mark.asyncio
async def test_auth_hello_success(authed_broker):
    result = await authed_broker.hello("kimi", "3", ["#phase-3"], auth_token="secret123")
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_auth_required_on_chat(authed_broker):
    await authed_broker.hello("kimi", "3", ["#phase-3"], auth_token="secret123")
    with pytest.raises(AuthError):
        await authed_broker.chat("kimi", "#phase-3", "hi")


@pytest.mark.asyncio
async def test_auth_chat_success(authed_broker):
    r = await authed_broker.hello("kimi", "3", ["#phase-3"], auth_token="secret123")
    result = await authed_broker.chat("kimi", "#phase-3", "hi", auth_token="secret123", session_token=r["session_token"])
    assert result["status"] == "ok"


# --- Validation tests -----------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_channel_name(broker):
    r = await broker.hello("kimi", "3", ["#phase-3"]); tok = r["session_token"]
    with pytest.raises(ValidationError):
        await broker.chat("kimi", "not-a-channel", "hi", session_token=tok)


@pytest.mark.asyncio
async def test_empty_body(broker):
    r = await broker.hello("kimi", "3", ["#phase-3"]); tok = r["session_token"]
    with pytest.raises(ValidationError):
        await broker.chat("kimi", "#phase-3", "", session_token=tok)


@pytest.mark.asyncio
async def test_invalid_agent_name(broker):
    with pytest.raises(ValidationError):
        await broker.hello("", "3", ["#phase-3"])


@pytest.mark.asyncio
async def test_chat_mirror_to_dispatch(broker):
    r = await broker.hello("kimi", "3", ["#dispatch"]); tok = r["session_token"]
    result = await broker.chat("kimi", "#dispatch", "chat mirrors", session_token=tok)
    assert result["status"] == "ok"

    with open(broker.dispatch_path, "r") as f:
        lines = f.readlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["type"] == "chat"
    assert entry["source"] == "kimi"
    assert entry["tier"] is None
    assert entry["phase"] == "3"
    assert entry["message"] == "chat mirrors"
    assert entry["resolved"] is False


@pytest.mark.asyncio
async def test_resolve_message_updates_dispatch(broker):
    r = await broker.hello("kimi", "3", ["#dispatch"]); tok = r["session_token"]
    await broker.chat("kimi", "#dispatch", "needs resolution", session_token=tok)

    # Find the message id
    conn = sqlite3.connect(str(broker.db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT id FROM messages WHERE body = ?", ("needs resolution",))
    row = cur.fetchone()
    conn.close()
    msg_id = row["id"]

    result = await broker.resolve_message("kimi", msg_id, session_token=tok)
    assert result["status"] == "ok"
    assert result["resolved"] is True

    with open(broker.dispatch_path, "r") as f:
        lines = f.readlines()
    # Only the resolved message remains in the file (single snapshot)
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["type"] == "chat"
    assert entry["resolved"] is True


@pytest.mark.asyncio
async def test_resolve_message_auth_rejects_non_author(broker):
    r = await broker.hello("kimi", "3", ["#dispatch"]); tok = r["session_token"]
    r = await broker.hello("claude", "2", ["#dispatch"]); tok2 = r["session_token"]
    await broker.chat("kimi", "#dispatch", "do not touch", session_token=tok)

    conn = sqlite3.connect(str(broker.db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT id FROM messages WHERE body = ?", ("do not touch",))
    row = cur.fetchone()
    conn.close()
    msg_id = row["id"]

    with pytest.raises(AuthError):
        await broker.resolve_message("claude", msg_id)


@pytest.mark.asyncio
async def test_dispatch_jsonl_no_duplicates_on_resolve(broker):
    r = await broker.hello("kimi", "3", ["#dispatch"]); tok = r["session_token"]
    await broker.chat("kimi", "#dispatch", "msg one", session_token=tok)

    conn = sqlite3.connect(str(broker.db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT id FROM messages WHERE body = ?", ("msg one",))
    row = cur.fetchone()
    conn.close()
    msg_id = row["id"]

    # Resolve and then unresolve — each should rewrite, not append
    await broker.resolve_message("kimi", msg_id, session_token=tok)
    await broker.unresolve_message("kimi", msg_id, session_token=tok)
    await broker.resolve_message("kimi", msg_id, session_token=tok)

    with open(broker.dispatch_path, "r") as f:
        lines = f.readlines()

    # Only one line per unique message id
    ids_seen = []
    for line in lines:
        entry = json.loads(line)
        ids_seen.append(entry["id"])

    assert ids_seen.count(msg_id) == 1
    # Last operation was resolve, so resolved should be True
    final_entry = json.loads(lines[-1])
    assert final_entry["resolved"] is True


# --- Concurrency stress test ----------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_listen_and_post(broker):
    r = await broker.hello("alice", "1", ["#general"]); tok = r["session_token"]
    r = await broker.hello("bob", "1", ["#general"]); tok2 = r["session_token"]

    received = []

    async def listener(name):
        result = await broker.listen(name, ["#general"], "full", 0, 2000, session_token=(tok if name == "alice" else tok2))
        if result.get("messages"):
            received.extend(result["messages"])

    # Start both listeners
    task_alice = asyncio.create_task(listener("alice"))
    task_bob = asyncio.create_task(listener("bob"))
    await asyncio.sleep(0.1)  # Let them park

    # Post a message
    await broker.chat("alice", "#general", "concurrent msg", session_token=tok)

    await asyncio.wait_for(asyncio.gather(task_alice, task_bob), timeout=3.0)

    # At least one listener should have received it
    assert any(m["body"] == "concurrent msg" for m in received)


@pytest.mark.asyncio
async def test_restart_invalidates_sessions():
    """Test 8: Broker restart clears all sessions; old token must rotate, not Case-1 ok."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        dispatch_path = Path(tmpdir) / "dispatch_comms.jsonl"
        dispatch_state_path = Path(tmpdir) / "dispatch_state.json"
        audit_path = Path(tmpdir) / "audit.log.jsonl"
        audit_cp = Path(tmpdir) / "audit.checkpoint"

        # First broker instance
        b1 = Broker(
            db_path=db_path, dispatch_path=dispatch_path,
            dispatch_state_path=dispatch_state_path,
            audit_path=audit_path, audit_checkpoint_path=audit_cp,
        )
        b1.set_lock(asyncio.Lock())
        r = await b1.hello("alice", "3", ["#general"])
        old_tok = r["session_token"]

        # Simulate restart: new Broker, same DB
        b2 = Broker(
            db_path=db_path, dispatch_path=dispatch_path,
            dispatch_state_path=dispatch_state_path,
            audit_path=audit_path, audit_checkpoint_path=audit_cp,
        )
        b2.set_lock(asyncio.Lock())

        # Old token should NOT trigger Case-1 (same process retry)
        # because agents table was cleared on startup
        r2 = await b2.hello("alice", "3", ["#general"], session_token=old_tok)
        assert r2["status"] == "ok"
        # Must be a NEW token, not the old one
        assert r2["session_token"] != old_tok

        # Old token should now fail chat
        with pytest.raises(AuthError):
            await b2.chat("alice", "#general", "should fail", session_token=old_tok)

        # New token should work
        await b2.chat("alice", "#general", "should work", session_token=r2["session_token"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
