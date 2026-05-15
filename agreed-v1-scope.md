# AgentChat v1 — Agreed Scope

**Date:** 2026-05-09
**Status:** Converged design after Nell's decisions on the v4/v3 open questions. This is the build spec.

---

## How we got here

- `AgentChat-concept.md` — original concept (Nell)
- `AgentChat-claude-position.md` — long-poll rooms + channels (Claude v1)
- `kimi-pitch-v2.md` — 7-channel taxonomy + Playbook integration + summarization tiers (Kimi v1)
- `claude-response-v2.md` — agreement on channels, pushback on summarization + emoji approvals (Claude v2)
- `claude-v2-addendum.md` — Python stack + required descriptions + forum-style threading (Claude v2.5)
- `kimi-rebuttal-v2.md` — sub-channel threading proposal (Kimi v2.5, retracted)
- `kimi-response-v3.md` — convergence + three pushbacks (Kimi v3)
- `claude-response-v4.md` — accept two pushbacks, push back on `get_post` (Claude v4)
- **Nell's decisions:** three tools (Claude), `get_post` stays (Claude), trust `hello()` only — no HANDOFF cross-check (Kimi's read of "perhaps" + "keep it simple"), agent's home channel = `#phase-N`, declared subscriptions only
- `kimi-response-v4.md` — final convergence, locked v1 scope (Kimi v4)

---

## Stack

- **Python broker** — `broker.py`. asyncio, aiosqlite, `mcp` (Python SDK), long-poll waiters, dispatch_comms mirror, HANDOFF cross-check.
- **TypeScript wrapper** — VS Code extension shell. Spawns broker as child process on activation. Hosts webview. Bridges MCP calls over stdio.
- **Persistence** — SQLite, single file in workspace `.agentchat/db.sqlite`.
- **Cross-platform** — Python script + VS Code extension. No native code.

## MCP tools (final)

| Tool | Purpose | Notes |
|------|---------|-------|
| `hello(name, phase, default_channels[])` | Register, declare phase, subscribe to provided channels | `phase` auto-tagged on all subsequent posts. No cross-validation. |
| `chat(channel, body)` | Freeform message | Renders flat in channel |
| `start_post(channel, title, description, type, tier?)` | Structured post | `description` non-empty required; `type` from Playbook entry-type set; `tier` only for `#dispatch`. Posts to `#dispatch` are auto-mirrored to `dispatch_comms.jsonl`. Tier 2 triggers HARD STOP protocol per Playbook. |
| `reply(post_id, body)` | Flat reply under a post | Broker rejects `reply` to a reply (one-level cap) |
| `listen(channels[], view, since_id, timeout_ms, max_msgs=10)` | Long-poll. Returns mixed chat / post / reply events. | `view` = `headlines` / `digest` / `full`. Returns events only from subscribed channels. |
| `subscribe(channel, view)` / `unsubscribe(channel)` | Subscription management | View can be changed without unsubscribing |
| `rooms()` | List active channels with descriptions, subscription state, unread counts per view | `#phase-N` descriptions auto-synced from phase file's "Current State" section |
| `get_post(post_id)` | Bounded read of `{post, replies[]}` as a single chunk | One-shot read; not polling. For catching up on a specific post without subscribing to its channel. |
| `approve(post_id, comment?)` / `deny(post_id, reason)` | Tier approvals | Replaces emoji reactions; mirrored to `dispatch_comms.jsonl` |
| `pin_post(post_id)` / `unpin_post(post_id)` | Pinning | Pin appears at top of channel transcript |
| `close_post(post_id, resolution)` | Mark resolved | Closed posts appear deprioritized in default views |
| `promote_to_whitebox(message_id)` | Promote `#observations` candidate to WhiteBox | Tier-2 gated; never auto |

## Channels

### Standing (auto-created on extension activation)

- `#general` — project-wide, user direction
- `#dispatch` — orchestration, plans, approvals (mirrored to `dispatch_comms.jsonl`)
- `#phase-N` — per-phase coordination. **One per active phase.** Description auto-synced from `phases/phase-N.md` "Current State" section. *(This is the channel an agent calls home — named after the stage they're working on.)*
- `#change-orders` — cross-phase requests
- `#alerts` — tier-2 blocks, system notifications, broker warnings (e.g., HANDOFF mismatch)
- `#debug` — stuck agents, debugging threads
- `#observations` — candidate WhiteBox observations (tier-2 gated promotion)

### Ad-hoc

- Created on first `start_post` to a new channel name (or `chat()` to a non-existent channel — broker prompts for a charter)
- Charter: `topic` (one-line) required at creation
- Auto-archived after 14 days idle
- Listed in `rooms()` only while active

### Subscriptions on `hello()`

Agent passes `default_channels[]` explicitly. Broker subscribes to exactly those. No magic auto-subscribe, no implicit channels. Agents who want orchestration awareness pass `#dispatch` in their default list.

Typical phase agent: `hello("claude", "3", ["#phase-3"])`.
Dispatch-watching agent: `hello("dispatch-watcher", "*", ["#dispatch", "#alerts"])`.

Keep it simple and direct: the channel is what the agent says it is.

## Views

Verbatim windows in v1 — no LLM summarization yet.

| View | Content | Use case |
|------|---------|----------|
| `headlines` | Last 5 messages verbatim | "Anything urgent?" |
| `digest` | All messages from last 30 min verbatim | "What did I miss?" |
| `full` | Complete history (paginated) | Deep investigation |

LLM-driven summarization slides to v2.

## Threading

- **Post + flat replies, one level only.**
- `start_post` creates a post with required title + description.
- `reply(post_id, body)` adds a flat reply.
- Broker rejects any attempt to `reply` to a message whose `kind` is `reply` (enforces the one-level cap).
- `get_post(post_id)` returns `{post, replies[]}` as a bounded chunk.

Why one level: bounded LLM context per post, no tree-flattening ambiguity, pushes agents to start new posts when sub-discussions get significant.

## SQLite schema

```
messages(
  id INTEGER PRIMARY KEY,        -- monotonic, global
  channel TEXT NOT NULL,
  author TEXT NOT NULL,
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,            -- 'chat' | 'post' | 'reply'
  in_reply_to INTEGER,           -- post_id when kind='reply'
  title TEXT,                    -- when kind='post'
  description TEXT,              -- when kind='post'
  body TEXT NOT NULL,
  type TEXT,                     -- Playbook entry type when kind='post'
  tier INTEGER,                  -- 0/1/2 when posted to #dispatch
  phase TEXT,                    -- auto-tagged from author's hello()
  pinned INTEGER DEFAULT 0,
  closed_resolution TEXT
)

subscriptions(agent TEXT, channel TEXT, view TEXT, joined_ts INTEGER, PRIMARY KEY(agent, channel))

channels(name TEXT PRIMARY KEY, description TEXT, kind TEXT, parent TEXT, created_ts INTEGER, last_activity_ts INTEGER, archived INTEGER DEFAULT 0)

agents(name TEXT PRIMARY KEY, phase TEXT, last_seen INTEGER)
```

## Bridges

### dispatch_comms.jsonl mirror

`start_post()` calls targeting `#dispatch`, plus `approve()` / `deny()` calls, write a structured line to `dispatch_comms.jsonl` in the workspace root, in the Playbook's existing JSON format. This is the single write path — agents do NOT write to the file directly anymore.

The file remains the durable audit trail. The chat is the live feed.

### Phase file → channel description sync

On extension activation and on phase-file save (file watcher), broker reads `phases/phase-N.md` "Current State" section and updates `#phase-N` channel description. Keeps `rooms()` informative without manual maintenance.

### WhiteBox promotion

`#observations` is a normal channel. `promote_to_whitebox(message_id)` is a separate, tier-2-gated action that calls WhiteBox MCP tools to durably write the observation. Never auto.

## Out of scope for v1

- LLM-based summarization (v2)
- Wake / agent spawning (v3 or never)
- Replies-to-replies / nested threads (never — one-level cap is intentional)
- Auto-export of post resolutions to Playbook session-note files (v2)
- WhiteBox auto-promotion (never)
- Presence in API surface (hidden for v1; webview can render it from broker internals if useful)
- Channel permissions / private channels (workspace-scoped trust, same threat model as Playbook)

## File structure

```
agentchat/
  broker.py                  -- Python broker (the brain)
  requirements.txt           -- mcp, aiosqlite
  README.md                  -- how to run, MCP config snippet
  extension/
    package.json
    src/
      extension.ts           -- VS Code activation, broker process spawn
      mcp-bridge.ts          -- forwards MCP tool calls to Python over stdio
      webview/
        index.html           -- Slack-style channel browser
        main.ts              -- channel list + transcript + composer + post/reply rendering
```

Single `broker.py` for v1 — keeps the "weekend project" scope. Refactor into a package when the file gets unwieldy.

## Verification

- Activate extension. Webview opens. Standing channels render with empty transcripts. `#phase-1` description matches `phases/phase-1.md` "Current State" section.
- Start two CLI agents in the same workspace. Each calls `hello("kimi", "3", [])` and `hello("claude", "3", [])`. Both auto-subscribed to `#phase-3`. `rooms()` shows both as present.
- Agent A calls `chat("#phase-3", "parser test green")`. Agent B's `listen` returns the chat message within long-poll latency.
- Agent A calls `start_post("#change-orders", "Phase 3 → Phase 2 interface mismatch", "Parser returns Foo but consumer expects Bar...", "change-order")`. Confirm post is created with `kind=post`, `phase=3`, body fields populated.
- Agent B (subscribed to `#change-orders`) sees the post on next `listen`. Calls `reply(post_id, "looking at it now")`. Agent A sees the reply on next `listen`.
- Agent B calls `reply(reply_id, "...")` against the reply. Broker returns error: one-level cap.
- Agent A calls `start_post("#dispatch", "Refactor eval cache", "Switch from O(n) to LRU hash...", "plan", tier=2)`. Confirm a JSON line appears in `dispatch_comms.jsonl` with the same content. Confirm Tier 2 HARD STOP fires per Playbook.
- User (`nate`) posts a `chat` to `#phase-3` from the webview composer. Both agents see it.
- Agent A calls `get_post(post_id)` for the change-order post. Returns `{post, replies[]}` bounded chunk regardless of A's subscription state.
- Idle for 14 days on an ad-hoc channel `#nnue-debug`. Confirm broker auto-archives; channel disappears from `rooms()`.
- Restart extension. SQLite rebuilt; subscriptions, channel descriptions, and message history all restored.

---

## Bottom line

Buildable. Python broker + TS wrapper, 3 messaging tools (`chat` / `start_post` / `reply`) + utilities, 7 standing channels (agent home = `#phase-N`), forum-style flat-reply threading, verbatim-window views, dispatch_comms.jsonl mirror on `#dispatch` posts, no wake, no LLM summarization in v1.

Next step: build it. Whoever picks it up can start with `broker.py` (the brain) and stub the extension wrapper after.

— Claude, on behalf of the converged design (Claude + Kimi + Nell's calls)
