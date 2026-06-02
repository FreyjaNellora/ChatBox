"""Bounded-autonomy guardrail tests (Appendix A.9 step 5 / A.7).

Detector units + supervisor escalation on each trip condition (livelock,
give-up, turn cap, crash). No model, no transport — runs on Python 3.9:
    python tests/test_guardrails.py
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agentchat/
from broker_core import Broker, ASLEEP
from guardrails import LivelockDetector, looks_like_give_up, content_hash
from supervisor import Supervisor


def make_broker(root: Path) -> Broker:
    return Broker(
        db_path=root / "broker.db",
        dispatch_path=root / "dispatch_comms.jsonl",
        dispatch_state_path=root / "dispatch_state.json",
        workspace_root=root,
        audit_path=root / "audit.jsonl",
        audit_checkpoint_path=root / "audit.checkpoint",
    )


def fixed_runner(output, did_work=True, session="s"):
    async def run(broker, agent):
        return {"did_work": did_work, "session_id": session, "output": output}
    return run


def varying_runner():
    n = {"i": 0}
    async def run(broker, agent):
        n["i"] += 1
        return {"did_work": True, "session_id": "s", "output": f"different-{n['i']}"}
    return run


def boom_runner():
    async def run(broker, agent):
        raise RuntimeError("kaboom")
    return run


class DetectorUnitTests(unittest.TestCase):
    def test_give_up_patterns(self):
        self.assertIsNotNone(looks_like_give_up("I'm not sure which one you mean"))
        self.assertIsNotNone(looks_like_give_up("Could you clarify the target?"))
        self.assertIsNone(looks_like_give_up("Done — posted the summary to #general."))
        self.assertIsNone(looks_like_give_up(""))

    def test_livelock_fires_on_repeat_and_resets(self):
        d = LivelockDetector(repeat_threshold=3)
        self.assertIsNone(d.record("same"))
        self.assertIsNone(d.record("same"))
        self.assertIsNotNone(d.record("same"))      # 3rd identical -> fire
        d2 = LivelockDetector(repeat_threshold=3)
        self.assertIsNone(d2.record("a"))
        self.assertIsNone(d2.record("b"))           # different -> counter resets
        self.assertIsNone(d2.record("b"))
        self.assertIsNotNone(d2.record("b"))

    def test_empty_output_is_not_a_loop(self):
        d = LivelockDetector(repeat_threshold=2)
        self.assertIsNone(d.record(""))
        self.assertIsNone(d.record(""))
        self.assertIsNone(d.record(""))

    def test_threshold_floor(self):
        with self.assertRaises(ValueError):
            LivelockDetector(repeat_threshold=1)


class EscalationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cbg_"))
        self.b = make_broker(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    async def _sup(self, runner, **kw):
        sup = Supervisor(self.b, managed=["claude"], watch_channels=["#general"],
                         turn_runner=runner, **kw)
        await sup.start()
        return sup

    def _alerts(self):
        return [m for m in self.b._fetch_messages(["#alerts"], "full", 0, 100)
                if m["author"] == "supervisor"]

    async def test_livelock_escalates(self):
        sup = await self._sup(fixed_runner("spinning"), repeat_threshold=3, turn_cap=20)
        await sup._run_agent("claude")
        alerts = self._alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("livelock", alerts[0]["description"])
        self.assertEqual(self.b.get_liveness("claude")["state"], ASLEEP)

    async def test_give_up_escalates(self):
        sup = await self._sup(fixed_runner("I'm not sure which one you mean"), turn_cap=20)
        await sup._run_agent("claude")
        self.assertEqual(len(self._alerts()), 1)
        self.assertIn("gave up", self._alerts()[0]["description"])

    async def test_turn_cap_escalates(self):
        sup = await self._sup(varying_runner(), turn_cap=4, repeat_threshold=99)
        await sup._run_agent("claude")
        self.assertEqual(len(self._alerts()), 1)
        self.assertIn("turn cap", self._alerts()[0]["description"])

    async def test_crash_escalates_and_unwedges(self):
        sup = await self._sup(boom_runner())
        await sup._run_agent("claude")
        self.assertEqual(len(self._alerts()), 1)
        self.assertIn("error", self._alerts()[0]["description"])
        self.assertEqual(self.b.get_liveness("claude")["state"], ASLEEP, "not wedged in BUSY")

    async def test_clean_quiescence_does_not_escalate(self):
        sup = await self._sup(fixed_runner("ok", did_work=False))
        await sup._run_agent("claude")
        self.assertEqual(self._alerts(), [], "no escalation on a normal finish")

    async def test_custom_on_escalate_hook(self):
        seen = {}
        async def hook(broker, agent, reason):
            seen["agent"] = agent
            seen["reason"] = reason
        sup = await self._sup(fixed_runner("spinning"), repeat_threshold=2, on_escalate=hook)
        await sup._run_agent("claude")
        self.assertEqual(seen.get("agent"), "claude")
        self.assertIn("livelock", seen.get("reason", ""))
        self.assertEqual(self._alerts(), [], "hook replaces the default alert post")


if __name__ == "__main__":
    unittest.main(verbosity=2)
