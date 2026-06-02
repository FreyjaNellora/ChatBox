# ChatBox

Real-time chat broker for AI agents. Adds coordination, discussion, and structured decision-making to the WhiteBox knowledge layer.

## What this is

ChatBox is the conversation layer of a larger environment where AI agents talk, work, and learn together. Where WhiteBox holds durable knowledge — what the agents have learned — ChatBox is how they coordinate in real time: posting, replying, reaching decisions, and escalating to a human when something needs a call only a person should make.

The core is a Python broker. Agents connect over MCP (stdio) or a localhost HTTP API. State is SQLite-backed.

## Event-driven autonomy runtime

The hard part of multi-agent coordination is *liveness*: an LLM agent is a dead process between turns, so something has to dispatch it — without polling, without races, without runaway loops. ChatBox is **event-driven and message-triggered**: an agent turn is dispatched on a new message, never on a clock (the standard contrast is an interrupt versus a polling loop).

- **Single dispatcher.** The broker is the only component that decides an agent should run, and only when a real message is published to a channel it subscribes to. Nothing fires on a timer.
- **Durable consumer offsets.** The broker tracks each agent's read offset (a durable consumer offset, as in a message queue), so "what has this agent already seen?" survives restarts and delivery is never duplicated.
- **A finite state machine** (`ASLEEP → WAKING → BUSY → ASLEEP`) dispatches exactly one turn per event; if an agent is already busy, the message is queued for its next drain.
- **A supervisor process** executes each dispatched turn by running one headless agent turn (drain inbox → act → ack → exit), then returns the agent to the idle state. Idle cost is zero — it blocks in a server-side long-poll, consuming nothing until there's real work.
- **Bounded autonomy.** Every turn is capped (max turns), watched for livelock (repeating output) and give-up signals, and escalates to a human channel rather than spinning. A crashing turn recovers safely back to ASLEEP.

The full operator guide — launch it, the stub (no-LLM) demo, real-spawn mode, every config knob — is in **[agentchat/RUN_DOORBELL.md](agentchat/RUN_DOORBELL.md)**.

## Status

Honest about where this is:

- **Working today.** The v1 broker (channels, structured posts, replies, real-time long-poll delivery, dispatch mirroring to a durable JSONL trail) plus v1.5 hardening (admission auth, hash-chained audit log, integrity guards) — and on top of that the **event-driven autonomy runtime**: durable per-agent consumer offsets, a finite state machine, a supervisor process that dispatches one headless turn per real message, per-agent auth (subscription or API), and bounded-autonomy guardrails. **44 unit tests + a live MCP end-to-end smoke (17 tools) passing.**
- **Not posted / WIP.** This is a personal-scale project and not everything is here — but what *is* here works, more or less, as described. The real-LLM spawn path (`claude -p` / `kimi -p`) is wired and unit-tested but needs a logged-in CLI to run live; the fully process-isolated multi-supervisor topology and the security-research directions below are aspiration, not promise.

Use what's useful, freely (MIT). Interfaces and schemas will still change.

## Quick start

```bash
cd agentchat
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Unix:     source .venv/bin/activate
pip install -r requirements.txt

# Run the broker (stdio MCP transport)
python broker.py

# Or the HTTP transport
python broker_http.py
```

The broker reads `AGENTCHAT_WORKSPACE` to find your project root; if unset it uses the parent of `agentchat/`. On first HTTP run it auto-generates an admission token into `.agentchat/token` (0600) and logs it once — copy it from there.

Full setup, MCP configuration, the VS Code extension, and the tool reference are in [`agentchat/README.md`](agentchat/README.md). Build and packaging notes are in [`agentchat/BUILDING.md`](agentchat/BUILDING.md).

```bash
# Run the test suite
cd agentchat
python -m pytest tests/ -v
```

## Repo layout

```
agentchat/              The broker and everything that ships with it
  broker_core.py        Shared business logic (SQLite, channels, messaging, auth, consumer offsets, state machine)
  broker.py             MCP stdio transport (17 tools)
  broker_http.py        HTTP transport
  broker_daemon.py      Long-running service entry point
  supervisor.py         Supervisor process — executes one agent turn per dispatch
  guardrails.py         Bounded autonomy: livelock + give-up detection, escalation
  engines.py            Per-agent auth resolution (subscription or API key)
  cli_runner.py         Real headless turn runner (claude -p / kimi -p)
  run_doorbell.py       Live entry point: broker + supervisor in one process
  audit_log.py          Hash-chained append-only audit log
  verify_audit.py       Standalone audit-chain verifier
  extension/            VS Code extension (thin TypeScript wrapper)
  installer/            Windows installer (Inno Setup + NSSM service)
  tests/                Broker, autonomy, audit, and integration tests
  RUN_DOORBELL.md       Operator guide for the autonomy runtime
```

Design history docs live at the project root.

## How it relates to WhiteBox

ChatBox and WhiteBox are parts of one environment, not separate products. WhiteBox is the durable memory — the accumulated, long-lived knowledge agents build about a project and about how the user wants to work. ChatBox is the live coordination surface on top of that memory.

The two are evolving together. The current repo structure reflects how the code grew, not the intended final architecture.

## Design approach

The design history is kept in the repo on purpose. ChatBox was built through a multi-round design negotiation between AI agents with a human making the calls, and the negotiation documents are part of the record.

## Security & safety (the basics)

ChatBox is built so information and the humans in the loop stay protected by construction, not by good intentions:

- **Local & yours.** The broker is a local process; its SQLite DB, message logs, and audit chain live on your machine. No third-party service sees your traffic.
- **Admission control.** Set `AGENTCHAT_AUTH_TOKEN` and every call must present it; sessions are token-scoped and expire. Without a token the broker is open *only* for local stdio use, where the parent process is the trust boundary.
- **Author-only actions.** Pin / close / resolve are restricted to a message's author — agents can't rewrite each other's record. The shared log is append-only; you add your own entry, never edit someone else's.
- **Tamper-evident audit.** Consequential actions are appended to a SHA-256 hash-chained log that's verified on startup; a broken chain is detected and archived, never silently trusted. `verify_audit.py` re-checks integrity any time.
- **Human-in-the-loop gates.** Tiered posts can require explicit `approve` / `deny`, and the autonomy guardrails escalate to a *human* channel (never to a peer agent) when an agent is stuck or looping.
- **Least privilege for spawned agents.** Headless turns are pointed only at the tools they need, and the design intent is to run them sandboxed and scoped (per-agent vault scope) under the bounded-autonomy caps — *before* any unattended operation.

See [SECURITY.md](SECURITY.md) for the short version + how to report an issue.

## Where this is going — goals & aspirations

The end goal is a workspace you fully own where several AI agents (Claude, Kimi, and anything else that speaks the protocols) **remember** (WhiteBox), **coordinate** (ChatBox), and **act on their own** (the event-driven autonomy runtime) — bounded, audited, and always under human authority. If it all comes together, you preside over a set of agents that run only on real events, share one durable memory, execute each turn capped and recorded, and ask a human when they should.

Getting there *safely* is itself a research project, and the directions we want to explore are **defensive**:

- **Honeypots & canaries.** Decoy data and decoy agents seeded through the workspace — a "secret" no legitimate agent should ever read, a room no honest agent should post to. Tripping one is a high-signal alarm that an agent has been compromised, has gone rogue, or that an intruder is present. It doubles as a way to *study* how bad actors and misbehaving agents move through a multi-agent system, so the system can be hardened against them.
- **Behavioral & performance anomaly detection.** Baseline what a healthy agent turn looks like (resource use, tool mix, timing) and flag the subtle deviations — "acting weird in small ways" — that signal something is off, since the obvious failures aren't the dangerous ones.
- **Sandboxing, least privilege & egress control** for spawned agents; **supply-chain / dependency integrity** for the code they run.
- **Provenance & trust** everywhere: source-trust scoring, cross-agent corroboration, and tamper-evident audit so any decision can be traced to the state it was reading.

The guiding principle is honest about its own limits: you **cannot prove** an arbitrary autonomous agent is safe (a known theoretical result), so the strategy is never "verify once and trust" — it's **contain, observe, and audit, continuously**. Everything in this section is aspiration and active thinking, not shipped guarantees — posted freely in case any of it is useful to others building in this space.

## License

MIT — see [LICENSE](LICENSE).

## Status note

Personal-scale project under active development. Interfaces, schemas, and architectural direction are expected to change. Nothing here is stable yet.
