#!/usr/bin/env python3
"""Run the doorbell hub: broker + supervisor in ONE process.

The "make the autonomy runtime actually RUN" entry point. It instantiates a
broker_core.Broker and a Supervisor and runs the supervisor's event-driven poll
loop, so a message posted by a human (or another agent) wakes exactly one
headless turn per managed agent — the doorbell, live.

This is the single-process mode of the hub (broker + waker + answerer together).
The fully blast-isolated multi-process topology (a separate supervisor talking
to broker_daemon over HTTP) is a later step (Appendix A.1); this single-process
mode is the simplest thing that runs and is what the integration test drives.

Config via environment:
  AGENTCHAT_WORKSPACE   project root (broker DB / audit live under here)
  AGENTCHAT_AUTH_TOKEN  optional broker auth token
  DOORBELL_MANAGED      comma-separated agent names the supervisor may spawn (required)
  DOORBELL_WATCH        comma-separated channels to watch (default: #general,#dispatch)
  DOORBELL_ENGINE       claude|kimi -> REAL spawn via cli_runner. Unset -> STUB demo.
  DOORBELL_AUTH_MODE    subscription|api (default subscription)   [real mode]
  DOORBELL_SECRET       api key/token when auth_mode=api          [real mode]
  DOORBELL_MODEL        optional model id override                [real mode]
  DOORBELL_POLL_MS      long-poll timeout per tick (default 30000)

Run:  python run_doorbell.py        (set DOORBELL_MANAGED=claude first)
"""
import asyncio
import logging
import os
import sys

sys.dont_write_bytecode = True

from broker_core import Broker
from supervisor import Supervisor, make_stub_turn_runner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("agentchat.doorbell")


def _csv(name, default):
    v = os.environ.get(name)
    return [x.strip() for x in v.split(",") if x.strip()] if v else list(default)


async def build_doorbell(*, broker=None, managed=None, watch=None, engine=None,
                         auth_mode="subscription", secret=None, model=None):
    """Wire a broker + supervisor. Returns (broker, supervisor), supervisor started.

    Injectable for tests (pass a broker + explicit managed/watch). In STUB mode
    (no engine) the managed agents are pre-registered so the stub can act as
    them; in REAL mode each spawned agent self-registers via its own `hello`.
    """
    if broker is None:
        broker = Broker(auth_token=os.environ.get("AGENTCHAT_AUTH_TOKEN") or None)
        broker.set_lock(asyncio.Lock())
    managed = managed if managed is not None else _csv("DOORBELL_MANAGED", [])
    watch = watch if watch is not None else _csv("DOORBELL_WATCH", ["#general", "#dispatch"])
    if not managed:
        raise ValueError("DOORBELL_MANAGED is empty — name at least one agent to supervise")
    engine = engine if engine is not None else os.environ.get("DOORBELL_ENGINE")

    if engine:
        from cli_runner import make_cli_turn_runner
        runner = make_cli_turn_runner({
            "engine": engine, "auth_mode": auth_mode,
            "secret": secret, "model": model,
        })
        logger.info("Real-spawn mode: engine=%s auth=%s", engine, auth_mode)
    else:
        sessions = {}
        for a in managed:
            r = await broker.hello(a, "agent", watch)
            sessions[a] = r["session_token"]
        runner = make_stub_turn_runner(sessions)
        logger.warning("STUB demo mode (no DOORBELL_ENGINE) — agents echo-reply, no LLM.")

    sup = Supervisor(broker, managed=managed, watch_channels=watch, turn_runner=runner)
    await sup.start()
    return broker, sup


async def amain():
    poll_ms = int(os.environ.get("DOORBELL_POLL_MS", "30000"))
    try:
        broker, sup = await build_doorbell(
            auth_mode=os.environ.get("DOORBELL_AUTH_MODE", "subscription"),
            secret=os.environ.get("DOORBELL_SECRET"),
            model=os.environ.get("DOORBELL_MODEL"),
        )
    except ValueError as exc:
        logger.error("%s", exc)
        return
    logger.info("Doorbell running. managed=%s watch=%s. Waiting for events (no clock)...",
                sorted(sup.managed), sup.watch)
    await sup.run_forever(timeout_ms=poll_ms)


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
