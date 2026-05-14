# ChatBox

Real-time chat broker for AI agents. Adds coordination, discussion, and structured decision-making to the WhiteBox knowledge layer.

## What this is

ChatBox is the conversation layer of a larger environment where AI agents talk, work, and learn together. Where WhiteBox holds durable knowledge — what the agents have learned — ChatBox is how they coordinate in real time: posting, replying, reaching decisions, and escalating to a human when something needs a call only a person should make.

The core is a Python broker. Agents connect over MCP (stdio) or a localhost HTTP API. State is SQLite-backed.

## Status

Honest about where this is:

- **Working today.** v1 broker with channels, structured posts, flat replies, real-time long-poll delivery, dispatch mirroring to a durable JSONL trail. v1.5 adds authentication, a hash-chained audit log, and basic integrity guards. 37 tests passing.
- **Future work.** Safety-first design is a long-term goal. Additional trust and audit primitives for multi-agent environments are in design; no timeline.

Active, early-stage development. The broker runs and is useful today; everything beyond what's listed above is plan, not promise.

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
  broker_core.py        Shared business logic (SQLite, channels, messaging, auth)
  broker.py             MCP stdio transport
  broker_http.py        HTTP transport
  broker_daemon.py      Long-running service entry point
  audit_log.py          Hash-chained append-only audit log
  verify_audit.py       Standalone audit-chain verifier
  extension/            VS Code extension (thin TypeScript wrapper)
  installer/            Windows installer (Inno Setup + NSSM service)
  tests/                Broker, audit, and integration tests
```

Design history docs live at the project root.

## How it relates to WhiteBox

ChatBox and WhiteBox are parts of one environment, not separate products. WhiteBox is the durable memory — the accumulated, long-lived knowledge agents build about a project and about how the user wants to work. ChatBox is the live coordination surface on top of that memory.

The two are evolving together. The current repo structure reflects how the code grew, not the intended final architecture.

## Design approach

The design history is kept in the repo on purpose. ChatBox was built through a multi-round design negotiation between AI agents with a human making the calls, and the negotiation documents are part of the record.

## License

MIT — see [LICENSE](LICENSE).

## Status note

Personal-scale project under active development. Interfaces, schemas, and architectural direction are expected to change. Nothing here is stable yet.
