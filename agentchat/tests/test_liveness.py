"""Unit tests for the liveness state machine (Appendix A.9 step 2).

Pure broker_core logic — no `mcp`, no transport, no spawning — so it runs on
the stock Python 3.9 here:  python tests/test_liveness.py
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agentchat/
import broker_core
from broker_core import Broker, ASLEEP, WAKING, BUSY


def make_broker(root: Path) -> Broker:
    return Broker(
        db_path=root / "broker.db",
        dispatch_path=root / "dispatch_comms.jsonl",
        dispatch_state_path=root / "dispatch_state.json",
        workspace_root=root,
        audit_path=root / "audit.jsonl",
        audit_checkpoint_path=root / "audit.checkpoint",
    )


class LivenessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cblive_"))
        self.b = make_broker(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    async def _register(self, name="tester", channels=None):
        r = await self.b.hello(name, "test", channels or ["#general"])
        return r["session_token"]

    def test_default_is_asleep(self):
        self.assertEqual(self.b.get_liveness("nobody")["state"], ASLEEP)

    def test_set_preserves_session_unless_cleared(self):
        self.b.set_liveness("a", BUSY, session_id="sess-1")
        self.assertEqual(self.b.get_liveness("a"), {"state": BUSY, "session_id": "sess-1", "waking_ts": None})
        # marking ASLEEP with session_id=None keeps the resume handle
        self.b.set_liveness("a", ASLEEP)
        self.assertEqual(self.b.get_liveness("a")["session_id"], "sess-1")
        # "" explicitly clears it
        self.b.set_liveness("a", ASLEEP, session_id="")
        self.assertIsNone(self.b.get_liveness("a")["session_id"])

    def test_invalid_state_rejected(self):
        with self.assertRaises(broker_core.ValidationError):
            self.b.set_liveness("a", "NOPE")

    async def test_compute_wakes_idempotent_and_cursor_gated(self):
        st = await self._register()
        mid = (await self.b.chat("tester", "#general", "ping", session_token=st))["message_id"]

        woken = self.b.compute_wakes("#general", mid)
        self.assertEqual(woken, ["tester"], "ASLEEP + unread -> woken once")
        self.assertEqual(self.b.get_liveness("tester")["state"], WAKING)

        self.assertEqual(self.b.compute_wakes("#general", mid), [], "already WAKING -> not re-woken")

        # Back to ASLEEP but cursor now caught up -> no wake.
        self.b.set_liveness("tester", ASLEEP)
        await self.b.ack("tester", "#general", mid, session_token=st)
        self.assertEqual(self.b.compute_wakes("#general", mid), [], "cursor caught up -> no wake")

        # A newer message re-arms the doorbell.
        mid2 = (await self.b.chat("tester", "#general", "ping2", session_token=st))["message_id"]
        self.assertEqual(self.b.compute_wakes("#general", mid2), ["tester"])

    async def test_busy_enqueues_instead_of_waking(self):
        st = await self._register()
        mid = (await self.b.chat("tester", "#general", "ping", session_token=st))["message_id"]
        self.b.set_liveness("tester", BUSY, session_id="sess-x")
        self.assertEqual(self.b.compute_wakes("#general", mid), [], "BUSY agent is not re-spawned")
        self.assertEqual(self.b.get_liveness("tester")["state"], BUSY, "stays BUSY; message stays durable")

    async def test_restart_resets_state_keeps_session_and_cursor(self):
        st = await self._register()
        await self.b.ack("tester", "#general", 7, session_token=st)
        self.b.set_liveness("tester", BUSY, session_id="sess-keep", waking_ts=123.0)

        b2 = make_broker(self.root)  # constructor runs _reconcile_liveness_on_start
        live = b2.get_liveness("tester")
        self.assertEqual(live["state"], ASLEEP, "no spawned turns survive a restart")
        self.assertEqual(live["session_id"], "sess-keep", "resume handle preserved")
        self.assertIsNone(live["waking_ts"], "stale waking_ts cleared")
        self.assertEqual(b2._get_cursor("tester", "#general"), 7, "cursor untouched by reconcile")


if __name__ == "__main__":
    unittest.main(verbosity=2)
