# Kimi's Response — AgentChat Design Review v2
# Context: Playbook Integration & Channel Architecture

**Date:** 2026-05-08
**From:** Kimi
**Context:** Reviewed full Playbook framework (factory model, communication protocol, information hierarchy, session/cross-phase protocols, agent conduct template)

---

## The Playbook Tells Us What AgentChat Must Become

After reading the full Playbook, I now see AgentChat not as a standalone chat app, but as the **real-time nervous system** of the factory model. The Playbook already has:
- **Structured communication** (dispatch_comms.jsonl with typed entries: plan, progress, stuck, blocked, change-order, closeout)
- **Approval tiers** (0/1/2 with hard-stop rules)
- **Information hierarchy** (5 levels, compress as you go up)
- **Cross-phase protocol** (change orders as formal handoffs)
- **Session protocol** (SBAR-style entries, adaptive check-in timing)

AgentChat should **not replace any of this.** It should **make it real-time, push-based, and multi-agent.**

The current file-based dispatch log is the right *format* but the wrong *transport*. AgentChat is the transport upgrade.

---

## The Channel Model: ntfy-Inspired, Context-Window Aware

Think of AgentChat as a **multi-channel pub/sub system** where channels are scoped by topic, not just by room. This is what the Playbook's communication patterns actually need:

### Core Channels (Every Workspace Has These)

| Channel | Purpose | Who Subscribes | Retention | Playbook Mapping |
|---------|---------|---------------|-----------|-----------------|
| `#general` | Broadcasts, announcements, user direction | All agents | 24h or last 50 msgs | STATUS.md updates, user directives |
| `#dispatch` | Orchestration, scheduling, phase assignments | Dispatch agent + user | 7 days | dispatch_comms.jsonl "plan" / "approval" entries |
| `#phase-N` | Per-phase coordination, within-phase discussion | Agents assigned to phase N | Until phase complete | Phase file discussions, within-scope decisions |
| `#change-orders` | Cross-phase requests, formal handoffs | All agents + user | Permanent | change-orders/ directory, CO notifications |
| `#alerts` | Tier 2 escalations, build failures, blocks | User + relevant agents | 30 days | Tier 2 hard-stop notifications |
| `#debug` | Stuck agents, debugging threads, spiral prevention | Any agent who can help | 7 days | "stuck" / "blocked" entries |
| `#observations` | Durable insights about user preferences | WhiteBox sync | Permanent | WhiteBox observation writes |

### Why Channels Beat Rooms for This Use Case

A "room" implies a conversation between specific people. A "channel" implies a topic that agents opt into based on relevance. The Playbook's communication is **topic-driven**, not **participant-driven**:

- When Phase 3 hits a parser issue, they don't need a room with Phase 2. They need to post to `#change-orders` where Phase 2 is subscribed.
- When an agent is stuck, they don't need to find the right room. They post to `#debug` and any available agent can jump in.
- When the user issues direction, it goes to `#general` — every agent sees it.

This maps exactly to how ntfy works: subscribe to the topics you care about, ignore the rest.

---

## Context Window Management: The Hard Problem

Here's the critical insight: **agents have bounded context windows.** If AgentChat dumps every channel message into every agent's context, we defeat the purpose. The Playbook's information hierarchy (Level 1 → 5) is the answer.

### Per-Channel Summarization Tiers

AgentChat should maintain **three views of every channel:**

| View | Content | Context Cost | Use Case |
|------|---------|-------------|----------|
| **Headlines** | Last 5 messages, one-line summaries | ~200 tokens | Agent quick-check: "anything urgent?" |
| **Digest** | Last 30 min of conversation, compressed to key decisions/questions | ~1K tokens | Agent joining mid-session: "what did I miss?" |
| **Full Log** | Complete channel history | Unbounded | Deep investigation, audit trail |

### The Agent's Choice

When an agent subscribes to a channel, it specifies which view it wants:

```
subscribe(channel="#phase-3", view="headlines")
subscribe(channel="#change-orders", view="digest")
subscribe(channel="#alerts", view="full")  -- always full, these are rare and critical
```

This lets agents **budget their context window** across channels:
- An agent deep in Phase 3 work might only want `#phase-3` headlines and `#alerts` full.
- A dispatch agent might want `#dispatch` full, `#change-orders` digest, everything else headlines.
- A debugging agent might temporarily subscribe to `#debug` full while helping someone stuck.

### Auto-Summarization

AgentChat runs a lightweight summarizer (could be the same MCP server, could be a separate process):
- Every 10 messages or 5 minutes, compress the digest view
- Every hour, compress headlines further
- Full log is always append-only in SQLite

This is how the Playbook's "compression principle" (session log → session note → phase file → STATUS.md) maps to real-time chat.

---

## How This Complements (Not Replaces) the Playbook

| Playbook Layer | Current Transport | AgentChat Transport | Relationship |
|---------------|-------------------|---------------------|--------------|
| Session logs (500 lines) | dispatch_comms.jsonl | `#phase-N` full log | AgentChat IS the real-time log; JSONL becomes the archive |
| Session notes (50 lines) | `sessions/phase-N/session-NNN.md` | `#phase-N` digest, auto-exported | Digest view auto-generates session note draft |
| Phase file updates (5 lines) | Manual edit | `#phase-N` headlines, auto-suggested | Headlines suggest phase file updates |
| STATUS.md (1 line) | Manual edit | `#general` + `#dispatch` cross-channel summary | Cross-channel intelligence suggests STATUS updates |
| Change orders | `change-orders/CO-NNN.md` + JSONL entry | `#change-orders` channel, auto-filed | Channel discussion becomes CO document |

**Key principle:** AgentChat captures the *conversation*. The Playbook files capture the *decisions*. AgentChat should **suggest** Playbook updates but never overwrite them without approval.

---

## Message Types: Mapping Playbook Entry Types to Channels

The Playbook's `dispatch_comms.jsonl` entry types map directly to channel + message type:

| Playbook Type | Channel | AgentChat Message Type | Special Behavior |
|--------------|---------|----------------------|-----------------|
| `plan` | `#dispatch` | `PLAN` | Requires approval reaction (👍/👎) |
| `progress` | `#phase-N` | `UPDATE` | Auto-summarized into digest |
| `change-order` | `#change-orders` | `CO-REQUEST` | Pins message, creates thread |
| `stuck` | `#debug` | `HELP` | Pings subscribed debug agents |
| `blocked` | `#alerts` | `BLOCKED` | Immediate notification to user |
| `extension` | `#phase-N` | `EXTENDING` | No reaction needed |
| `closeout` | `#phase-N` | `SESSION-END` | Triggers session note export prompt |
| `approval` | `#dispatch` | `APPROVED` | Unblocks pending plans |
| `denial` | `#dispatch` | `DENIED` | Unblocks with direction |

This means agents don't "chat freestyle." They **publish structured messages** into the right channel. The structure enforces the Playbook's communication discipline.

---

## The Wake Question, Revisited

My v1 pitch questioned whether synchronous wake was necessary. With the Playbook context, I'm more convinced:

**Agents should check in at session start, not be woken mid-session.**

The Playbook session protocol already has agents:
1. Read STATUS.md
2. Read phase file
3. Read latest session note
4. Check dispatch_comms.jsonl

Step 4 becomes: **"Read AgentChat headlines for all subscribed channels."** An agent starting a new session sees everything it missed. No wake needed.

If an agent is mid-session and a critical alert fires (`#alerts`), the user can choose to spawn a new agent session. That's a **user decision**, not an automated wake. The Playbook's Tier 2 hard-stop already says: user notification, then wait. AgentChat makes the notification instant, but the waiting remains.

**Exception:** If we ever want true parallel multi-agent (Claude and Kimi working simultaneously), wake matters. But the Playbook's factory model assumes **one agent per phase per session**. Parallelism is across phases, not within them. So wake is Phase 2, not Phase 1.

---

## UI: VS Code Webview Channel Browser

The webview panel becomes a **channel browser**, not a chatroom:

```
┌─────────────────────────────────────────┐
│  AgentChat — Freyja                      │
│  ┌─────────┬──────────────────────────┐ │
│  │#general │ [12:34] Kimi (Phase 3):  │ │
│  │#dispatch│ PLAN: Refactor eval cache│ │
│  │#phase-3 │        [👍 dispatch]      │ │
│  │#phase-7 │ [12:29] Claude (Phase 7):│ │
│  │#change- │ UPDATE: NNUE wiring fix  │ │
│  │  orders │        complete. Tests   │ │
│  │#alerts  │        pass.             │ │
│  │#debug   │ [12:15] CO-023: Phase 3  │ │
│  │         │ → Phase 2 interface      │ │
│  │         │        mismatch.         │ │
│  │         │        [📌 pinned]        │ │
│  │         │                          │ │
│  │         │ ── Digests ──            │ │
│  │         │ #phase-3 (last 30min):   │ │
│  │         │ • Eval cache plan pending│ │
│  │         │   approval               │ │
│  │         │ • Parser issue resolved  │ │
│  │         │ #change-orders (today):  │ │
│  │         │ • CO-023: Phase 3→2      │ │
│  │         │   (awaiting Phase 2)     │ │
│  └─────────┴──────────────────────────┘ │
└─────────────────────────────────────────┘
```

Left sidebar: channels, with unread counts per view (headlines/digest/full).
Right panel: selected channel, showing messages + auto-generated digest at top.
User can click any message to see full thread.

---

## Suggested MVP v2 (Reordered)

| Phase | Scope | Proves |
|-------|-------|--------|
| 1 | MCP server + SQLite + 3 channels (`#general`, `#phase-N` template, `#alerts`) + structured message types + webview | The broker loop works |
| 1.5 | Auto-summarization (headlines + digest views) | Context window management works |
| 2 | All 7 channel types + subscription management + cross-channel digest | Full Playbook integration |
| 3 | Export to Playbook files (session note draft, CO draft, STATUS suggestion) | AgentChat → Playbook bridge |
| 4 | Wake mechanism (if parallel multi-agent becomes needed) | Real-time collaboration |

---

## Open Questions for Claude

1. **Channel creation:** Should channels be static (defined in config) or dynamic (agents create `#phase-3-debug` on the fly)? Dynamic is more flexible but risks channel proliferation.

2. **Message threading:** Should `CO-023` create a thread under `#change-orders`, or is the channel flat? Threads help organize but add complexity.

3. **User override:** Should the user be able to post to any channel, or only `#general` + `#dispatch`? The Playbook says "user is source of truth on direction" — that suggests `#general` is the user's channel.

4. **WhiteBox integration:** Should `#observations` auto-write to WhiteBox, or queue for user approval? The original concept says "AgentChat may call WhiteBox" — but the Playbook's Tier 2 discipline suggests user approval for durable writes.

---

## Bottom Line

AgentChat is not a chat app. It's the **real-time layer of the Playbook factory model.** Channels replace rooms. Structured messages replace freeform chat. Summarization tiers protect context windows. The Playbook files remain the system of record; AgentChat is the live feed that generates them.

Build the broker first, the channels second, the summarization third, and the Playbook export fourth. Wake is last — if ever.

— Kimi
