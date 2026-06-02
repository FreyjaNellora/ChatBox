#!/usr/bin/env python3
"""AgentChat Supervisor — the doorbell "answerer" (Appendix A.9 step 3).

Deliberately separate from the broker: the broker is the single always-on
WAKER; the supervisor CONSUMES wake decisions and owns running an agent turn.
That split is the blast-radius isolation from the plan — a runaway or crashing
spawned turn must not be able to take down the only waker.

This skeleton is engine-agnostic via a pluggable `turn_runner`, so the same
orchestration drives:
  - a STUB turn now (no LLM, fully unit-testable on stock Python 3.9), and
  - a real `query()`/`claude -p`/`kimi -p` spawn later (step 4) — a drop-in
    replacement for `turn_runner`, nothing else changes.

Event-driven, not clock-driven: poll_once() blocks inside the broker's
server-side long-poll (`listen`) until a real message arrives. With no traffic
it costs nothing; there is no busy polling.

A turn_runner is an async callable run_turn(broker, agent_name) -> dict with:
    {"did_work": bool, "session_id": Optional[str]}
`did_work=False` signals quiescence (inbox drained) so the supervisor stops
self-continuing and returns the agent to ASLEEP.
"""
from typing import Awaitable, Callable, Optional

from broker_core import ASLEEP, BUSY
from guardrails import LivelockDetector, looks_like_give_up

TurnRunner = Callable[..., Awaitable[dict]]


class Supervisor:
    def __init__(self, broker, managed, watch_channels, turn_runner,
                 name: str = "supervisor", turn_cap: int = 8,
                 repeat_threshold: int = 3, escalation_channel: str = "#alerts",
                 on_escalate: Optional[Callable[..., Awaitable[None]]] = None):
        self.broker = broker
        self.managed = set(managed)          # agents this supervisor may spawn
        self.watch = list(watch_channels)    # channels it watches for traffic
        self.turn_runner = turn_runner
        self.name = name
        self.turn_cap = turn_cap             # max self-continues per wake (A.7)
        self.repeat_threshold = repeat_threshold  # livelock sensitivity (A.7)
        self.escalation_channel = escalation_channel
        self.on_escalate = on_escalate       # override the default tier-2 alert
        self.session: Optional[str] = None
        self._cursor = 0                     # in-memory scan position over message ids

    async def start(self):
        r = await self.broker.hello(self.name, "system", self.watch)
        self.session = r["session_token"]

    async def poll_once(self, timeout_ms: int = 50) -> list:
        """One doorbell tick: drain new messages, decide wakes, run turns.

        Returns the list of agents handled this tick. With a large timeout_ms
        this blocks until a message arrives (event-driven); with a small one it
        is a non-blocking check (handy for tests)."""
        res = await self.broker.listen(
            self.name, self.watch, "full", self._cursor, timeout_ms, session_token=self.session
        )
        handled = []
        for m in res["messages"]:
            self._cursor = max(self._cursor, m["id"])
            if m["author"] == self.name:
                continue  # ignore our own chatter
            woken = self.broker.compute_wakes(m["channel"], m["id"], candidates=self.managed)
            for agent in woken:
                await self._run_agent(agent)
                handled.append(agent)
        return handled

    async def run_forever(self, timeout_ms: int = 30000):
        """Long-poll loop. Each tick blocks server-side until traffic arrives."""
        if self.session is None:
            await self.start()
        while True:
            await self.poll_once(timeout_ms=timeout_ms)

    async def _run_agent(self, agent: str):
        """Drive one agent through WAKING -> BUSY -> (turns) -> ASLEEP, bounded.

        compute_wakes() already moved it ASLEEP -> WAKING; here we confirm the
        spawn (WAKING -> BUSY) and run self-continuing turns under the A.7
        guardrails — all enforced HERE so they hold whichever turn_runner (stub
        or real query()) is plugged in:
          * turn cap     — max self-continues per wake;
          * livelock     — identical output repeated repeat_threshold times;
          * give-up      — the turn's text matches an "I can't / which one?" pattern.
        Any trip stops the loop and escalates to a human. The agent ALWAYS
        returns to ASLEEP — even if the turn_runner raises — so it can be
        re-woken later rather than wedging in WAKING/BUSY."""
        prev = self.broker.get_liveness(agent)
        session_id = prev["session_id"]
        self.broker.set_liveness(agent, BUSY, session_id=session_id)
        detector = LivelockDetector(self.repeat_threshold)
        escalate_reason = None
        try:
            turns = 0
            while True:
                if turns >= self.turn_cap:
                    escalate_reason = f"turn cap reached ({self.turn_cap} turns without quiescing)"
                    break
                turns += 1
                result = await self.turn_runner(self.broker, agent)
                if result.get("session_id"):
                    session_id = result["session_id"]
                output = result.get("output") or ""
                give_up = looks_like_give_up(output)
                if give_up:
                    escalate_reason = f"agent gave up / asked for help ({give_up})"
                    break
                loop = detector.record(output)
                if loop:
                    escalate_reason = f"livelock detected ({loop})"
                    break
                if not result.get("did_work"):
                    break  # clean quiescence — inbox drained
        except Exception as exc:  # a crashing turn must not wedge the agent
            escalate_reason = f"turn runner error: {exc!r}"
        finally:
            self.broker.set_liveness(agent, ASLEEP, session_id=session_id)
        if escalate_reason:
            await self._escalate(agent, escalate_reason)

    async def _escalate(self, agent: str, reason: str):
        """Hand control to a human. Default: a tier-2 alert on #alerts.

        Escalation targets a human/privileged channel, never a peer — in this
        broker ANY agent can approve/deny, so a peer must not be the arbiter."""
        if self.on_escalate is not None:
            await self.on_escalate(self.broker, agent, reason)
            return
        await self.broker.start_post(
            self.name, self.escalation_channel,
            title=f"Agent '{agent}' needs a human",
            description=reason, msg_type="alert", tier=2,
            session_token=self.session,
        )


def make_stub_turn_runner(sessions: dict, reply: str = "ack") -> TurnRunner:
    """A no-LLM turn runner for step 3 — faithful to what a real headless turn
    does over MCP (drain inbox -> reply -> ack), minus the model.

    `sessions` maps agent_name -> session_token. A real spawned process owns its
    own session via `hello`; here we inject it so the stub can act as the agent.
    """
    async def run(broker, agent) -> dict:
        st = sessions[agent]
        chans = list(broker.agents[agent].subscriptions.keys())
        res = await broker.listen(agent, chans, "full", 0, 50, session_token=st)
        msgs = res["messages"]
        if not msgs:
            return {"did_work": False, "session_id": f"stub-{agent}", "output": ""}
        last = msgs[-1]
        body = f"{reply}:{last['id']}"
        rep = await broker.chat(agent, last["channel"], body, session_token=st)
        high = rep["message_id"]  # global max id now (ids are monotonic)
        for ch in chans:
            await broker.ack(agent, ch, high, session_token=st)
        return {"did_work": True, "session_id": f"stub-{agent}", "output": body}

    return run
