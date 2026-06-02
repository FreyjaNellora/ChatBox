"""Integration test for the runnable hub wiring (run_doorbell.build_doorbell).

Stub mode, no model, no transport — runs on Python 3.9:
    python tests/test_run_doorbell.py
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agentchat/
from broker_core import Broker, ASLEEP
from run_doorbell import build_doorbell


def make_broker(root: Path) -> Broker:
    return Broker(
        db_path=root / "broker.db",
        dispatch_path=root / "dispatch_comms.jsonl",
        dispatch_state_path=root / "dispatch_state.json",
        workspace_root=root,
        audit_path=root / "audit.jsonl",
        audit_checkpoint_path=root / "audit.checkpoint",
    )


class DoorbellWiringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cbdoor_"))
        self.b = make_broker(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    async def test_build_and_run_one_tick(self):
        # The hub wires itself (stub mode pre-registers the managed agent).
        _broker, sup = await build_doorbell(broker=self.b, managed=["claude"], watch=["#general"])
        human = await self.b.hello("human", "user", ["#general"])
        await self.b.chat("human", "#general", "hi claude", session_token=human["session_token"])

        handled = await sup.poll_once()
        self.assertEqual(handled, ["claude"], "the doorbell woke the managed agent")
        self.assertEqual(self.b.get_liveness("claude")["state"], ASLEEP, "agent returned to sleep")
        msgs = self.b._fetch_messages(["#general"], "full", 0, 100)
        self.assertTrue(any(m["author"] == "claude" for m in msgs), "agent posted a reply")

    async def test_requires_managed_agents(self):
        with self.assertRaises(ValueError):
            await build_doorbell(broker=self.b, managed=[], watch=["#general"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
