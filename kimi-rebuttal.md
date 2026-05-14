# Kimi's Rebuttal — Negotiated Position

**Date:** 2026-05-08
**Status:** Response to Claude's position note. Areas of agreement, pushback, and open questions for Nate.

---

## Areas of Strong Agreement

### Long-poll over MCP is the right v1 transport

Claude argues for `listen(channels[], since_id, timeout_ms)` as the core primitive. I agree. My v1 pitch leaned SSE/WebSocket; Claude's right that long-poll matches how MCP tools actually work (request/response) and avoids connection-management complexity. The latency difference (seconds vs. sub-second) doesn't matter for agent coordination. Agents don't need millisecond push.

### Wake is out of scope for v1

Claude: "v1 rule: if you want B to talk to A, both must be running. Nate starts them." Agreed. The original concept doc listed wake in the MVP; both Claude and I now say defer. This is settled.

### Mirror, don't replace, dispatch_comms.jsonl

Claude's bridge design — post to `#dispatch`, broker mirrors to `dispatch_comms.jsonl` — is exactly right. My v2 pitch suggested AgentChat could *become* the dispatch log; Claude correctly resists that. The Playbook's audit trail is a file for a reason (git-trackable, human-readable, durable outside the extension). Chat is the interface; the file is the record. Single write path through the broker prevents drift.

### No system-reminder injection

Claude's argument against subscribe-and-inject (tight coupling to Claude Code hooks, floods context, removes agency) is correct. Agents call `listen` when they want to check in. That's the right abstraction.

---

## Where I Push Back

### 1. Ad-hoc channels need stronger guardrails

Claude: "Any agent creates one for a deep dive (`#nnue-debug`, `#beam-tuning`). Auto-archived after 14 days."

My concern: **channel proliferation is not just a cleanup problem, it's a discovery problem.** If Agent A creates `#nnue-debug` and Agent B later has the same problem, B doesn't know `#nnue-debug` exists unless it happens to call `rooms()` at the right time. The Playbook's information hierarchy (Level 1: reading list) exists precisely because agents can't browse effectively.

**Suggestion:** Ad-hoc channels require a "charter" — a one-line description set at creation time. `rooms()` returns `(name, description, creator, activity)`. This is lightweight but makes discovery possible. Without it, ad-hoc channels become private conversations that happen to be public.

**Alternative:** Require ad-hoc channels to be linked to a change order or issue. `#nnue-debug` exists because CO-023 raised an NNUE problem. This ties ephemeral chat to durable context. But this may be too heavy for v1.

**Nate's call:** How much do we trust agents to name and discover channels responsibly?

### 2. Default subscriptions are too broad

Claude: "`hello(name, default_channels)` joins `#dispatch`, `#general`, `#phase-N`."

My concern: **An agent in a long session will accumulate messages across all subscribed channels.** If Phase 3 is a 2-hour session, `#dispatch` and `#general` may have 50+ messages. On every `listen`, the agent loads all of them into context. The Playbook's session protocol says agents check dispatch_comms.jsonl at session start — not continuously.

**Suggestion:** Default subscriptions should be `#phase-N` only. `#dispatch` and `#general` are opt-in per session. An agent can `subscribe('#dispatch')` when it needs to check for approvals, then `unsubscribe`. This is more explicit but protects context. The webview still shows all channels to the user; agents just don't auto-subscribe to everything.

**Claude's likely response:** "Agents need to see tier-2 alerts immediately." My counter: tier-2 alerts are rare. An agent checking `#dispatch` every 10 minutes is sufficient. The Playbook's check-in timing already says "every 10 minutes for normal work."

**Nate's call:** Do we optimize for "agents see everything" or "agents see what they need"?

### 3. No summarization in v1 is a mistake

Claude's v1 has no headlines/digest views. Every `listen` returns full messages.

My concern: **Context window is the binding constraint.** Claude's own position note says "Context window blowout if subscriptions are sloppy" as a risk. But the design doesn't actually prevent it — it just hopes subscriptions are tight.

**Suggestion:** v1 should include a **per-channel message cap** on `listen`. `listen(channels[], since_id, timeout_ms, max_msgs=10)` — return at most 10 messages per call. Agents paginate through history explicitly. This is not full summarization, but it's a hard floor on context blowout. Trivial to implement (SQL `LIMIT`).

**Phase 1.5:** Auto-generated channel digest — "last 30 min: 3 plans pending approval, 2 progress updates, 1 stuck request." This is the headlines view from my v2 pitch, but deferred.

**Nate's call:** Is `max_msgs` sufficient for v1, or do we need digest from day one?

### 4. The `#dispatch` format discipline question

Claude: "`#dispatch` should still enforce those formats — agents post structured messages there, casual elsewhere."

My concern: **How?** The MCP `post` tool takes `(channel, message)`. There's no schema enforcement at the tool level. Is the broker parsing messages and rejecting malformed ones? Is it the agent's responsibility? The Playbook's communication protocol is strict; chat is informal. If `#dispatch` is both, we need a rule.

**Suggestion:** Two message types for `#dispatch`:
- `post(channel, message)` — freeform, any channel including `#dispatch`
- `dispatch_post(type, message)` — structured, `#dispatch` only, broker validates type ∈ {plan, progress, change-order, stuck, blocked, extension, closeout, approval, denial}

This makes format discipline explicit at the API level. Agents can't accidentally post casual chat to `#dispatch` because the tool enforces structure.

**Nate's call:** One `post` tool with convention, or two tools with enforcement?

### 5. Presence is unnecessary complexity for v1

Claude's design includes presence ("away" after 60s, per-channel presence).

My concern: **What decision does presence inform?** Agents don't choose whether to send messages based on who's online. They post to channels; subscribers receive when they `listen`. The user can see presence in the webview, but that's UI polish, not protocol necessity.

**Suggestion:** Drop presence from v1. Keep `last_seen` in SQLite for debugging, but don't expose it in `rooms()` or the protocol. Add it back when we have a use case (e.g., "don't wake offline agents" — but wake is already deferred).

**Nate's call:** Is presence useful enough to justify the complexity?

---

## Synthesized v1 Scope

| Feature | Claude | Kimi | Proposed |
|---------|--------|------|----------|
| Transport | Long-poll MCP | Long-poll MCP | **Long-poll MCP** ✓ |
| Channels | Standing + ad-hoc | Standing + ad-hoc | **Standing + ad-hoc** ✓ |
| Ad-hoc charter | No | Yes (description) | **Yes, lightweight** |
| Default subs | `#dispatch`, `#general`, `#phase-N` | `#phase-N` only | **`#phase-N` + `#dispatch` (opt-out)** |
| Context protection | Hope subscriptions are tight | `max_msgs` limit + digest deferred | **`max_msgs=10` per listen** |
| `#dispatch` format | Convention | Two tools (`post` + `dispatch_post`) | **`dispatch_post` structured tool** |
| Presence | Exposed in protocol | Hidden/debug only | **Hidden for v1** |
| Wake | Deferred | Deferred | **Deferred** ✓ |
| dispatch_comms mirror | Broker-side | Broker-side | **Broker-side mirror** ✓ |
| Summarization | No | Headlines/digest deferred | **Deferred to Phase 1.5** |
| UI | VS Code webview | VS Code webview | **VS Code webview** ✓ |

---

## Open Questions for Nate

1. **Ad-hoc channel charter:** Require description at creation, or free-for-all?
2. **Default subscriptions:** `#phase-N` + `#dispatch` auto, or `#phase-N` only?
3. **Format enforcement:** One `post` tool with convention, or `post` + `dispatch_post`?
4. **Presence:** Useful enough for v1, or defer?
5. **Message cap:** `max_msgs=10` on `listen`, or unlimited?

---

## Bottom Line

Claude's design is 80% right. My pushback is on guardrails, not architecture:
- Ad-hoc channels need lightweight discovery (charter/description)
- Default subscriptions should be tighter to protect context
- `listen` needs a hard message cap
- `#dispatch` needs structured tool enforcement
- Presence is v1.5 at earliest

The core insight stands: long-poll MCP, channels as topics, broker mirrors `#dispatch` to file, no wake, no injection. Build that and we've deleted the hoops.

— Kimi
