"""End-to-end doorbell test with a STUB turn (Appendix A.9 step 3).

Proves the full loop with NO model and NO transport, on stock Python 3.9:
    human posts -> broker decides wake -> supervisor runs exactly one turn
    -> agent drains/replies/acks/exits -> back to ASLEEP, no double-spawn.

    python tests/test_supervisor.py
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agentchat/
from broker_core import Broker, ASLEEP, BUSY
from supervisor import Supervisor, make_stub_turn_runner


def make_broker(root: Path) -> Broker:
    return Broker(
        db_path=root / "broker.db",
        dispatch_path=root / "dispatch_comms.jsonl",
        dispatch_state_path=root / "dispatch_state.json",
        workspace_root=root,
        audit_path=root / "audit.jsonl",
        audit_checkpoint_path=root / "audit.checkpoint",
    )


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cbsup_"))
        self.b = make_broker(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    async def _hello(self, name, channels):
        return (await self.b.hello(name, "test", channels))["session_token"]

    async def _setup(self):
        human_st = await self._hello("human", ["#general"])
        claude_st = await self._hello("claude", ["#general"])
        sup = Supervisor(
            self.b, managed=["claude"], watch_channels=["#general"],
            turn_runner=make_stub_turn_runner({"claude": claude_st}),
        )
        await sup.start()
        return human_st, claude_st, sup

    def _claude_replies(self):
        msgs = self.b._fetch_messages(["#general"], "full", 0, 100)
        return [m for m in msgs if m["author"] == "claude"]

    async def test_doorbell_end_to_end(self):
        human_st, _claude_st, sup = await self._setup()
        posted = await self.b.chat("human", "#general", "hi claude", session_token=human_st)

        handled = await sup.poll_once()
        self.assertEqual(handled, ["claude"], "exactly the managed agent is woken")

        live = self.b.get_liveness("claude")
        self.assertEqual(live["state"], ASLEEP, "agent returns to ASLEEP after its turn")
        self.assertEqual(live["session_id"], "stub-claude", "resume handle persisted")

        self.assertEqual(len(self._claude_replies()), 1, "agent posted exactly one reply")
        self.assertGreaterEqual(
            self.b._get_cursor("claude", "#general"), posted["message_id"],
            "agent's cursor advanced past the message it handled",
        )

    async def test_human_observer_is_never_woken(self):
        human_st, _claude_st, sup = await self._setup()
        await self.b.chat("human", "#general", "hi claude", session_token=human_st)
        await sup.poll_once()
        # 'human' subscribes to #general too but is NOT managed -> never spawned,
        # and must not be left transitioned to WAKING.
        self.assertEqual(self.b.get_liveness("human")["state"], ASLEEP)

    async def test_no_double_spawn_on_next_tick(self):
        human_st, _claude_st, sup = await self._setup()
        await self.b.chat("human", "#general", "hi claude", session_token=human_st)
        self.assertEqual(await sup.poll_once(), ["claude"])
        # Claude's own reply is new traffic, but it acked past it -> no re-wake.
        self.assertEqual(await sup.poll_once(), [], "quiescent: no re-spawn")
        self.assertEqual(len(self._claude_replies()), 1, "still exactly one reply")

    async def test_quiet_channel_wakes_nobody(self):
        _human_st, _claude_st, sup = await self._setup()
        self.assertEqual(await sup.poll_once(), [], "no messages -> no wakes")
        self.assertEqual(self.b.get_liveness("claude")["state"], ASLEEP)

    async def test_busy_agent_is_not_respawned(self):
        human_st, _claude_st, sup = await self._setup()
        # Pretend claude is mid-turn already.
        self.b.set_liveness("claude", BUSY, session_id="live-sess")
        await self.b.chat("human", "#general", "second message", session_token=human_st)
        self.assertEqual(await sup.poll_once(), [], "BUSY agent is enqueued, not respawned")
        self.assertEqual(self.b.get_liveness("claude")["state"], BUSY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
