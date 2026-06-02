#!/usr/bin/env python3
"""
End-to-end smoke test: spawn broker as a subprocess, talk to it via MCP stdio
the same way a real agent would.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call(session: ClientSession, tool: str, args: dict) -> dict:
    """Call an MCP tool and unwrap the JSON text content."""
    result = await session.call_tool(tool, args)
    text = result.content[0].text
    return json.loads(text)


async def main():
    workspace = tempfile.mkdtemp(prefix="agentchat-e2e-")
    print(f"[setup] workspace = {workspace}")

    broker_path = Path(__file__).resolve().parent.parent / "broker.py"
    python_exe = sys.executable

    params = StdioServerParameters(
        command=python_exe,
        args=[str(broker_path)],
        env={**os.environ, "AGENTCHAT_WORKSPACE": workspace},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("[init] MCP session established")

            tools = await session.list_tools()
            print(f"[tools] broker exposes {len(tools.tools)} tools")
            assert len(tools.tools) == 17, f"expected 17 tools, got {len(tools.tools)}"

            # 1. hello
            r = await call(session, "hello", {
                "name": "claude",
                "phase": "3",
                "default_channels": ["#phase-3", "#general"],
            })
            print(f"[hello] {r}")
            assert r["status"] == "ok"
            assert "#phase-3" in r["subscribed"]
            session_tok = r["session_token"]

            # 2. rooms — confirm phase channel was created standing
            r = await call(session, "rooms", {})
            phase3 = next((c for c in r["channels"] if c["name"] == "#phase-3"), None)
            assert phase3 is not None
            assert phase3["is_standing"], "#phase-3 should be standing"
            print(f"[rooms] #phase-3 standing={phase3['is_standing']}")

            # 3. chat
            r = await call(session, "chat", {
                "agent_name": "claude",
                "channel": "#phase-3",
                "body": "hello from claude over real MCP stdio",
                "session_token": session_tok,
            })
            print(f"[chat] message_id={r.get('message_id')}")
            assert r["status"] == "ok"

            # 4. listen — should return the message we just posted
            r = await call(session, "listen", {
                "agent_name": "claude",
                "channels": ["#phase-3"],
                "view": "full",
                "since_id": 0,
                "timeout_ms": 500,
                "session_token": session_tok,
            })
            print(f"[listen] got {len(r['messages'])} messages")
            assert len(r["messages"]) == 1
            assert r["messages"][0]["body"] == "hello from claude over real MCP stdio"
            assert r["messages"][0]["author"] == "claude"

            # 4b. ack the message over MCP, then re-listen — the durable cursor
            # should hide it (proves the new ack tool + effective_since drain).
            msg_id = r["messages"][0]["id"]
            r = await call(session, "ack", {
                "agent_name": "claude",
                "channel": "#phase-3",
                "up_to_id": msg_id,
                "session_token": session_tok,
            })
            assert r["status"] == "ok" and r["last_read_id"] == msg_id
            r = await call(session, "listen", {
                "agent_name": "claude",
                "channels": ["#phase-3"],
                "view": "full",
                "since_id": 0,
                "timeout_ms": 200,
                "session_token": session_tok,
            })
            assert len(r["messages"]) == 0, "cursor should hide the acked message"
            print("[ack] cursor advanced over MCP; re-listen returned 0")

            # 5. start_post + reply roundtrip
            r = await call(session, "start_post", {
                "agent_name": "claude",
                "channel": "#change-orders",
                "title": "CO-001: smoke test",
                "description": "Verifying real MCP stdio roundtrip works end-to-end",
                "type": "change-order",
                "session_token": session_tok,
            })
            post_id = r["post_id"]
            print(f"[start_post] post_id={post_id}")

            r = await call(session, "reply", {
                "agent_name": "claude",
                "post_id": post_id,
                "body": "self-replying for the test",
                "session_token": session_tok,
            })
            print(f"[reply] reply_id={r.get('reply_id')}")

            r = await call(session, "get_post", {"post_id": post_id})
            print(f"[get_post] {len(r['replies'])} replies")
            assert len(r["replies"]) == 1

            # 6. dispatch mirror
            r = await call(session, "start_post", {
                "agent_name": "claude",
                "channel": "#dispatch",
                "title": "PLAN: smoke test plan",
                "description": "Just verifying the mirror works.",
                "type": "plan",
                "tier": 1,
                "session_token": session_tok,
            })
            print(f"[dispatch] post_id={r['post_id']}")

            dispatch_path = Path(workspace) / "dispatch_comms.jsonl"
            assert dispatch_path.exists(), "dispatch_comms.jsonl should be in workspace root"
            with open(dispatch_path) as f:
                lines = f.readlines()
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["type"] == "plan"
            assert entry["tier"] == 1
            print(f"[dispatch] mirrored entry = {entry}")

    print("\n[PASS] all e2e smoke checks passed")


if __name__ == "__main__":
    asyncio.run(main())
