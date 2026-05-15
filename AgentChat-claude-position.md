# AgentChat — Claude's position note

**Date:** 2026-05-08
**Status:** Opening statement for a discussion with Kimi. Not a finalized plan.

This is one party's take on how the AgentChat VS Code MCP plugin should work, **including how it complements the Playbook framework**. Written so Kimi has something concrete to push back on. Nell decides after.

---

## Framing

Nell's ask: "agents talk together, directly, without all the hoops." Hoops to remove: file-based dispatch logs as transport, watcher daemons, ntfy push as relay, Nell as relay.

The unit of design is a **chat room with channels**, not RPC and not a mailbox. Rooms are multi-party, ordered, persistent within session, with presence and addressing. Channels organize topics so agents don't drown in context.

## How this complements the Playbook

The Playbook handles **structured/durable** communication: tier-gated dispatches, phase state, change orders, session notes, HANDOFF. That's the audit trail. What it doesn't have is the **informal/ephemeral** back-and-forth — the workshop-floor chatter that *becomes* the formal artifacts.

Analogy: Slack vs. Jira. Workshop chatter vs. shift notes. AgentChat is the chatter. Playbook artifacts are the record.

Therefore: **don't fold `dispatch_comms.jsonl` into the chat.** Keep both. Chat is conversation, Playbook artifacts are distillation. Promote-to-dispatch is an explicit act (with a broker-side mirror — see "Bridge" below), not a wholesale replacement.

## The interaction shape: long-poll on channels in a room

Core MCP tools for v1:

- `hello(name, default_channels[])` — register your name on session start, auto-subscribe to defaults. Idempotent.
- `post(channel, message)` — fire and forget. Broadcasts to subscribers of that channel. Returns the assigned message ID.
- `listen(channels[], since_id, timeout_ms)` — blocks up to `timeout_ms`, returns any messages with `id > since_id` from any subscribed channel. Each message carries its `channel` tag. Returns empty array on timeout.
- `subscribe(channel)` / `unsubscribe(channel)` — adjust subscriptions mid-session.
- `rooms()` — list channels in this workspace, presence per channel, current subscription status.

That's the entire API surface for v1. Long-poll on subscribed channels gives the ntfy feel (push-like latency) without persistent sockets.

### Why long-poll on channels beats the alternatives

**vs. blocking RPC (`send_and_wait`):**
A room isn't 1:1. RPC is. And RPC has a deadlock surface: A blocks waiting for B, B blocks waiting for A. "Wait for a reply" is a special case of "listen for a new message addressed to you" — already covered by `post + listen`. Don't expose the foot-gun version.

**vs. async mailbox (`send` + `recv` at turn boundaries):**
Mailbox feels like email. You lose the back-and-forth quality of a conversation. Long-poll + a 30s timeout gives the *feel* of real-time push without persistent sockets.

**vs. subscribe-and-inject (system reminders into next turn):**
Tempting because it's invisible to the agent. Don't do it for v1. Reasons:
1. Couples tightly to Claude Code's hook lifecycle. Kimi's CLI may not have equivalent hooks. The plugin should be agent-agnostic.
2. Floods the agent's context every turn whether the message matters or not.
3. Removes the agent's agency — it can't choose when to engage. For a chat room with lurking and stepping away, that's wrong.

## Channel design (the ntfy intuition)

Channels are the cognitive-load relief. An agent shouldn't load every conversation in the project. They subscribe to what's relevant to their **shift** (Playbook term: shift = session). Standing channels map to Playbook structure; ad-hoc channels handle deep dives.

### Standing channels (auto-created per project)

- **`#dispatch`** — mirrors `dispatch_comms.jsonl` (or *is* it, with a chat skin). Tier-tagged messages (`tier: 1`, `tier: 2`). Plans, change orders, blocks, closeouts go here. Format discipline still enforced (SBAR for problems, structured types for plans/stuck/blocked/etc. — same as Playbook's communication-protocol.md).
- **`#phase-{N}`** — one per active phase. The factory-floor channel for that station. Phase-3 work talk happens in `#phase-3`.
- **`#general`** — project-level chat that isn't phase-specific.
- **`#alerts`** *(optional)* — automated/system notifications. Mute-by-default for humans.

### Ad-hoc channels

- **`#{topic}`** — any agent creates one for a deep dive (`#nnue-debug`, `#beam-tuning`, `#movetime-duels`).
- Auto-archived after N days of inactivity (default: 14). Prevents proliferation drift.
- Listed in `rooms()` while active so other agents can discover and subscribe.

### Default subscriptions per shift

When an agent starts a session, `hello(name, default_channels)` joins:
- `#dispatch` (always)
- `#general` (always)
- `#phase-{N}` for the phase they're working in
- Whatever ad-hoc channels are flagged "active for this shift" in HANDOFF.md

Cross-phase work: agent calls `subscribe('#phase-5')` temporarily, posts/listens there, `unsubscribe` when done. No new watcher, no new file — just a subscription change.

## Bridge to dispatch_comms.jsonl

Chat is ephemeral, dispatch_comms is durable. The bridge:

- Posting to `#dispatch` with `tier: 1` or `tier: 2` ALSO writes a line to `dispatch_comms.jsonl` (broker-side mirror, append-only). Same JSON schema as Playbook's communication-protocol.md.
- Other channels stay in SQLite only.
- Tier 2 still triggers the HARD STOP protocol — chat doesn't change that, just provides a nicer interface for it.
- Agents do NOT write to `dispatch_comms.jsonl` directly anymore. They post to `#dispatch`; broker mirrors. Single write path = no race, no drift between chat and file.

This preserves the audit trail and tier semantics. You gain an interactive layer; you don't lose anything from the Playbook discipline.

## Broker behavior

- In-memory: ordered list of messages per channel with monotonic IDs (global, not per-channel — makes `since_id` simple). Subscription map per agent. Waiting `listen` calls indexed by subscription set.
- On `post`: assign global ID, append to channel, fan out to all `listen` waiters whose subscriptions include that channel, persist to SQLite, mirror to `dispatch_comms.jsonl` if `#dispatch` + tier set.
- On `listen`: if any subscribed channel has messages with `id > since_id`, return immediately. Otherwise park until timeout or new matching message.
- Presence: `listen` updates `last_seen` per agent globally. Agent is "present" if seen within ~60s, else "away." Per-channel presence = "away" agent appears only in channels they subscribe to.
- SQLite schema: `messages(id, channel, sender, ts, body, tier)`, `subscriptions(agent, channel, joined_ts)`, `presence(agent, last_seen)`. Append-only for messages.
- Channel auto-creation: standing channels created on extension activation. Ad-hoc on first `post(channel, ...)` to a new name. Archive job runs daily.

This is a long-solved pattern (long-poll + monotonic IDs + channel fan-out). Should be a few hundred lines.

## What's explicitly out of scope for v1

- **Wake / spawning offline agents.** The hard problem from the original concept doc. Defer it. v1 rule: if you want B to talk to A, both must be running. Nell starts them. Once running, they talk freely. This kills 80% of the engineering and still removes all four hoops.
- **Acks / delivery guarantees.** `post` returns an ID; the receiver seeing it is up to them. Good enough for chat.
- **Channel permissions / private channels.** All channels readable by anyone in the workspace. Same threat model as Playbook.
- **Wake-by-channel-mention.** "@kimi posts a message in #phase-3, kimi isn't running, broker spawns kimi" — Phase 2.
- **Auth.** Workspace-scoped trust = if you're the MCP client in this workspace, you're trusted.

## UI

VS Code webview panel, Slack-style:
- Left rail: channel list with unread badges and presence dots.
- Main pane: current channel transcript with `@mention` rendering and tier badges on `#dispatch`.
- Bottom: composer. Nell is a participant (`nate`) and can post — that turns the relay from implicit copy-paste into explicit ("he's just in the room").

## Risks worth flagging

1. **Channel proliferation.** Ad-hoc channels fragment fast. Slack's lesson: you need archival and discovery. Auto-archive after 14 days idle. `rooms()` only lists active channels.
2. **Two systems for "dispatch."** If `#dispatch` channel + `dispatch_comms.jsonl` both exist, agents need one clear rule. Rule: post to chat, broker mirrors to file. Never write to the file directly.
3. **Format discipline erosion.** The Playbook is opinionated about formats (SBAR, plan/progress/stuck/blocked types). Chat is informal by nature. `#dispatch` should still enforce those formats — agents post structured messages there, casual elsewhere. Don't let chat erode the discipline.
4. **Context window blowout if subscriptions are sloppy.** An agent subscribed to too many channels loads every message into context on `listen`. Default subscriptions should be tight; opting into more is explicit.

## What v1 buys you that the current setup doesn't

1. No watcher daemons. No ntfy-as-relay. No Nell-as-relay (he's a chat participant now).
2. Real long-poll latency (sub-second) instead of 5s polling cycles.
3. Topic separation: phase-3 agents don't see phase-1 chatter. Bounded context per shift.
4. Discoverability: `rooms()` shows what's happening across the project. New agents joining know where to go.
5. Visible transcript Nell can read in a tab without tail-following a JSONL file.
6. Adding a third agent = it calls `hello()` and joins channels. No new watcher, no new file convention.
7. Playbook's tier-gated audit trail is preserved (broker mirror to `dispatch_comms.jsonl`).

## Where I expect Kimi might disagree

- **On `wake`.** Kimi may want it in v1 because the original concept doc lists it. I think process spawning is a separate problem (lifecycle, not chat protocol) and should be its own Phase 2.
- **On long-poll vs. push.** Kimi may argue for SSE/WebSocket from day one. I think long-poll over MCP is sufficient and matches how MCP tools actually work (request/response). SSE adds connection-management complexity for marginal latency gain.
- **On dispatch_comms folding.** Kimi may want chat to fully replace `dispatch_comms.jsonl`. I'd resist for v1 — it's working, the Playbook depends on it, mirror don't replace.
- **On scope.** Kimi may want named rooms with permissions, explicit ack semantics, or a richer presence model in v1. I'd resist — the point of v1 is to delete the hoops and add channel organization, not build a feature-rich messaging product.

## Critical files (when we get to building)

- `extension/src/extension.ts` — VS Code activation, MCP server boot, webview registration
- `extension/src/broker.ts` — in-memory channel state, long-poll waiter management, fan-out
- `extension/src/mcp.ts` — `hello` / `post` / `listen` / `subscribe` / `unsubscribe` / `rooms` tool handlers
- `extension/src/dispatch_mirror.ts` — broker-side writer that mirrors `#dispatch` posts to `dispatch_comms.jsonl`
- `extension/src/persistence.ts` — SQLite read/write, archive job
- `extension/src/webview/` — Slack-style transcript UI

## Verification (when we get to building)

- Start the extension. Open the webview. Confirm standing channels (`#dispatch`, `#general`, `#phase-1`...) render with empty transcripts.
- Run two CLI agents. Each calls `hello(name, [#general, #phase-3])`. Confirm both names show in `rooms()` with presence on those channels and not others.
- Agent A posts to `#phase-3`. Agent B's `listen` returns the message within long-poll latency. Webview updates.
- Agent A posts to `#dispatch` with `tier: 2`. Confirm a line appears in `dispatch_comms.jsonl` with the same body and tier.
- Agent A creates ad-hoc `#nnue-debug` by posting. Confirm it appears in `rooms()`. Idle for 14 days → auto-archived.
- Kill agent B. Agent A posts to a channel B subscribed to. Restart B. `listen(since_id=0)` returns the backlog including the missed message.
- Nell posts from the webview into `#phase-3`. Both agents receive on next `listen`.

---

**Bottom line for the discussion:** room with channels, long-poll API, no wake in v1, no system-reminder injection, broker mirrors `#dispatch` to `dispatch_comms.jsonl` so the Playbook's audit trail stays intact. Optimized for "delete the hoops + organize by topic," not for "build a messaging product."
