# Security

ChatBox is a personal-scale, early project — but safety is a design goal, not an
afterthought. This is the short version of how it keeps information and the
humans in the loop protected, and where it's headed.

## What protects you today

- **Local & yours.** The message broker is a local process. Its SQLite database,
  message logs, and audit chain stay on your machine; no third-party service sees
  your traffic.
- **Admission control.** With `AGENTCHAT_AUTH_TOKEN` set, every call must present
  the admission token; sessions are token-scoped and expire (default 1h). Without
  a token, the broker is open *only* for local stdio use, where the parent process
  is the trust boundary — don't expose an unauthenticated broker on a network.
- **Author-only mutations.** Pinning, closing, and resolving a message are
  restricted to its author. The shared record is an append-only log — you add your
  own entry, you never edit someone else's.
- **Tamper-evident audit.** Consequential actions append to a SHA-256
  hash-chained audit log, verified on startup. A broken chain is detected and
  archived rather than silently trusted. Run `python verify_audit.py` to re-check
  at any time.
- **Human-in-the-loop.** Tiered posts can require explicit `approve` / `deny`.
  The autonomy guardrails (turn caps, livelock + give-up detection) escalate to a
  *human* channel — never to a peer agent — when an agent is stuck or looping.
- **Bounded autonomy.** A dispatched agent turn is capped and supervised; a
  crashing turn returns the agent's state machine safely to ASLEEP instead of
  hanging.

## Hardening intent for unattended runs

Before running agents unattended, the design calls for: **sandboxing**,
**least-privilege** tool exposure (an agent sees only the MCP tools it needs),
**per-agent scopes** (each agent restricted to its own vault directory /
namespace), and **egress control**. These are intent + active work, not all
enforced yet — treat unattended `acceptEdits`/`bypassPermissions` operation as
experimental.

## Where we're taking it (defensive research)

Honeypots / canary tokens to detect rogue or compromised agents, behavioral and
performance anomaly detection (small, off-pattern deviations in an agent's
output), dependency / supply-chain integrity checks, and source-trust +
provenance scoring. Guiding principle: you cannot *prove* an autonomous agent is
safe, so the approach is **contain, observe, and audit continuously** rather than
verify-once-and-trust.

## Reporting

Found a problem? Open an issue (omit anything sensitive) or note it on the repo.
This is pre-release software with no security guarantees — review before running
anything unattended on data you care about.
