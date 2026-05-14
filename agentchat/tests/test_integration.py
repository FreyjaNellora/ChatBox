#!/usr/bin/env python3
"""Integration test: two agents chatting through the broker core."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from broker_core import Broker


@pytest.mark.asyncio
async def test_two_agents_chatting():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        dispatch_path = Path(tmpdir) / "dispatch_comms.jsonl"
        dispatch_state_path = Path(tmpdir) / "dispatch_state.json"
        broker = Broker(db_path=db_path, dispatch_path=dispatch_path, dispatch_state_path=dispatch_state_path)
        broker.set_lock(asyncio.Lock())

        # Agent A (Claude, Phase 3) registers
        r = await broker.hello("claude", "3", ["#phase-3"]); ctok = r["session_token"]
        print(f"[Claude] hello: {r}")
        assert r["status"] == "ok"

        # Agent B (Kimi, Phase 7) registers
        r = await broker.hello("kimi", "7", ["#phase-7"]); ktok = r["session_token"]
        print(f"[Kimi] hello: {r}")
        assert r["status"] == "ok"

        # Claude posts to #phase-3
        r = await broker.chat("claude", "#phase-3", "Working on NNUE wiring today", session_token=ctok)
        print(f"[Claude] chat: {r}")
        msg_id_1 = r["message_id"]

        # Kimi listens on #phase-7 — should NOT see Claude's message
        r = await broker.listen("kimi", ["#phase-7"], "full", 0, 500, session_token=ktok)
        print(f"[Kimi] listen #phase-7: {len(r['messages'])} messages")
        assert len(r["messages"]) == 0, "Kimi should not see Phase 3 messages"

        # Kimi listens on #phase-3 — should see it
        r = await broker.listen("kimi", ["#phase-3"], "full", 0, 500, session_token=ktok)
        print(f"[Kimi] listen #phase-3: {len(r['messages'])} messages")
        assert len(r["messages"]) == 1
        assert r["messages"][0]["body"] == "Working on NNUE wiring today"
        assert r["messages"][0]["author"] == "claude"
        assert r["messages"][0]["phase"] == "3"

        # Claude starts a change order
        r = await broker.start_post(
            "claude", "#change-orders",
            "CO-023: Phase 3 → Phase 2 interface mismatch",
            "Parser returns Foo but Phase 2 expects Bar. Need epoch integers.",
            "change-order", tier=2,
            session_token=ctok
        )
        print(f"[Claude] start_post CO: {r}")
        co_id = r["post_id"]

        # Kimi subscribes to #change-orders and listens
        await broker.subscribe("kimi", "#change-orders", "full", session_token=ktok)
        r = await broker.listen("kimi", ["#change-orders"], "full", 0, 500, session_token=ktok)
        print(f"[Kimi] listen #change-orders: {len(r['messages'])} messages")
        assert len(r["messages"]) == 1
        assert r["messages"][0]["title"] == "CO-023: Phase 3 → Phase 2 interface mismatch"

        # Kimi replies to the CO
        r = await broker.reply("kimi", co_id, "Looking at it now, give me 10 min", session_token=ktok)
        print(f"[Kimi] reply: {r}")

        # Claude gets the reply
        r = await broker.listen("claude", ["#change-orders"], "full", co_id, 500, session_token=ctok)
        print(f"[Claude] listen for reply: {len(r['messages'])} messages")
        assert len(r["messages"]) >= 1

        # Claude fetches the full post + replies
        r = await broker.get_post(co_id)
        print(f"[Claude] get_post: {len(r['replies'])} replies")
        assert r["post"]["title"] == "CO-023: Phase 3 → Phase 2 interface mismatch"
        assert len(r["replies"]) == 1
        assert r["replies"][0]["body"] == "Looking at it now, give me 10 min"
        assert r["replies"][0]["author"] == "kimi"
        assert r["replies"][0]["phase"] == "7"

        # Verify dispatch_comms.jsonl has the tier-2 post
        r = await broker.hello("dispatch_tester", "1", ["#dispatch"]); dtok = r["session_token"]
        await broker.start_post(
            "dispatch_tester", "#dispatch",
            "PLAN: Test dispatch mirror",
            "This should appear in dispatch_comms.jsonl",
            "plan", tier=1,
            session_token=dtok
        )
        with open(dispatch_path, "r") as f:
            lines = f.readlines()
        print(f"[Dispatch] {len(lines)} entries in dispatch_comms.jsonl")
        # Filter to only the plan entry from dispatch_tester
        plan_lines = [l for l in lines if json.loads(l)["type"] == "plan"]
        assert len(plan_lines) == 1
        entry = json.loads(plan_lines[0])
        assert entry["type"] == "plan"
        assert entry["tier"] == 1
        assert entry["phase"] == "1"
        print(f"[Dispatch] entry: {entry}")

        # Test headlines view
        for i in range(12):
            await broker.chat("claude", "#phase-3", f"Flood message {i}", session_token=ctok)
        r = await broker.listen("claude", ["#phase-3"], "headlines", msg_id_1, 100, max_msgs=5, session_token=ctok)
        print(f"[Headlines] {len(r['messages'])} messages (capped at 5)")
        assert len(r["messages"]) == 5

        # Test digest view (last 30 min)
        r = await broker.listen("claude", ["#phase-3"], "digest", msg_id_1, 100, max_msgs=10, session_token=ctok)
        print(f"[Digest] {len(r['messages'])} messages")
        assert len(r["messages"]) <= 10

        print("\n[PASS] All integration tests passed!")


if __name__ == "__main__":
    asyncio.run(test_two_agents_chatting())
