"""Unit tests for the durable read-cursor primitive (Appendix A.9 step 1).

Pure broker_core logic — no `mcp` package, no transport — so it runs on the
stock Python 3.9 here:  python tests/test_cursor.py
"""
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agentchat/
import broker_core
from broker_core import Broker


def make_broker(root: Path) -> Broker:
    return Broker(
        db_path=root / "broker.db",
        dispatch_path=root / "dispatch_comms.jsonl",
        dispatch_state_path=root / "dispatch_state.json",
        workspace_root=root,
        audit_path=root / "audit.jsonl",
        audit_checkpoint_path=root / "audit.checkpoint",
    )


class CursorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cbtest_"))
        self.b = make_broker(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    async def _register(self, name="tester", channels=None):
        r = await self.b.hello(name, "test", channels or ["#general"])
        return r["session_token"]

    async def test_cursor_survives_restart_while_agents_wiped(self):
        st = await self._register()
        await self.b.ack("tester", "#general", 42, session_token=st)
        # Simulate a broker restart: a fresh Broker on the same files runs
        # _clear_stale_sessions() (DELETE FROM agents) in its constructor.
        b2 = make_broker(self.root)
        self.assertEqual(b2._get_cursor("tester", "#general"), 42, "cursor must persist")
        conn = sqlite3.connect(str(self.root / "broker.db"))
        agent_rows = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        conn.close()
        self.assertEqual(agent_rows, 0, "agents table is wiped on restart (the trap)")

    async def test_ack_is_monotonic_and_idempotent(self):
        st = await self._register()
        self.assertEqual((await self.b.ack("tester", "#general", 50, session_token=st))["last_read_id"], 50)
        self.assertEqual((await self.b.ack("tester", "#general", 30, session_token=st))["last_read_id"], 50)
        self.assertEqual((await self.b.ack("tester", "#general", 50, session_token=st))["last_read_id"], 50)
        self.assertEqual((await self.b.ack("tester", "#general", 80, session_token=st))["last_read_id"], 80)

    async def test_drain_respects_cursor(self):
        st = await self._register()
        ids = [(await self.b.chat("tester", "#general", f"m{i}", session_token=st))["message_id"] for i in range(3)]
        r = await self.b.listen("tester", ["#general"], "full", 0, 50, session_token=st)
        self.assertEqual([m["id"] for m in r["messages"]], ids, "all three visible before ack")

        await self.b.ack("tester", "#general", ids[-1], session_token=st)
        r = await self.b.listen("tester", ["#general"], "full", 0, 50, session_token=st)
        self.assertEqual(r["messages"], [], "cursor hides already-acked messages")
        self.assertTrue(r["timed_out"])

        new_id = (await self.b.chat("tester", "#general", "m3", session_token=st))["message_id"]
        r = await self.b.listen("tester", ["#general"], "full", 0, 50, session_token=st)
        self.assertEqual([m["id"] for m in r["messages"]], [new_id], "only the post-cursor message")

    async def test_no_ack_is_backward_compatible(self):
        st = await self._register()
        mid = (await self.b.chat("tester", "#general", "hello", session_token=st))["message_id"]
        # since_id floor still works exactly as before when no cursor exists.
        r = await self.b.listen("tester", ["#general"], "full", mid, 50, session_token=st)
        self.assertEqual(r["messages"], [], "client since_id still excludes <= mid")
        r = await self.b.listen("tester", ["#general"], "full", 0, 50, session_token=st)
        self.assertEqual([m["id"] for m in r["messages"]], [mid], "cursor=0 => old behavior")

    async def test_per_channel_independence(self):
        st = await self._register(channels=["#general", "#dispatch"])
        g = (await self.b.chat("tester", "#general", "g1", session_token=st))["message_id"]
        d = (await self.b.chat("tester", "#dispatch", "d1", session_token=st))["message_id"]
        await self.b.ack("tester", "#general", g, session_token=st)  # ack ONLY #general
        r = await self.b.listen("tester", ["#general", "#dispatch"], "full", 0, 50, session_token=st)
        ids = [m["id"] for m in r["messages"]]
        self.assertNotIn(g, ids, "acked channel hidden")
        self.assertIn(d, ids, "un-acked channel still drains")

    async def test_ack_validation_and_auth(self):
        st = await self._register()
        with self.assertRaises(broker_core.AuthError):
            await self.b.ack("tester", "#general", 5, session_token="bogus")
        with self.assertRaises(broker_core.ValidationError):
            await self.b.ack("tester", "#general", -1, session_token=st)
        with self.assertRaises(broker_core.ValidationError):
            await self.b.ack("tester", "not-a-channel", 5, session_token=st)


if __name__ == "__main__":
    unittest.main(verbosity=2)
