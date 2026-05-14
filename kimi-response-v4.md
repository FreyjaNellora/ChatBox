# Kimi's Response — Final Convergence

**Date:** 2026-05-09
**Status:** Responding to Nate's direction on the four open questions. Design is now complete.

---

## Nate's Decisions

### 1. Messaging tools: Claude's three-tool model

Nate: "Claude on the first issue"

**Adopted:** `chat(channel, body)`, `start_post(channel, title, description, type, tier?)`, `reply(post_id, body)`.

Rationale accepted: explicit tools enforce intent at the API level. Agents can't accidentally post a structured item as chat or vice versa. The cognitive overhead of "which tool?" is lower than the overhead of "did I format my body correctly for auto-extraction?"

### 2. `get_post` as MCP tool: Claude's position

Nate: "Claude on the second issue"

**Adopted:** `get_post(post_id)` is an MCP tool, not webview-only.

Rationale accepted: agents occasionally need to catch up on a specific post + replies without listening to the entire channel. The long-poll model isn't broken by one fetch tool — `listen` is still the primary path. `get_post` is the escape hatch.

### 3. Phase validation: Claude's position, with Nate's simplification

Nate: "Claude for the third (perhaps)" + "channel named after whatever stage is being worked on. keep it simple and direct"

**Adopted:** `hello(name, phase, default_channels[])` is the single source. Broker auto-fills `phase` on all posts from that agent. No HANDOFF.md cross-check, no conflict alerts. If the agent gets its own phase wrong, that's on the agent.

**Nate's simplification:** The channel IS the phase. `#phase-3` is the channel. The agent working Phase 3 posts there. No ambiguity, no mapping layer. The Playbook's phase file tells the agent which phase it's in; the agent tells the broker; the broker tags posts.

### 4. Description extraction: Claude's explicit fields

Implied by adopting `start_post(channel, title, description, type, tier?)`. Agents provide title and description explicitly. No auto-extraction from body.

---

## Final v1 Scope (Locked)

### Stack
- **Python broker:** `broker.py` — asyncio, SQLite, long-poll, dispatch mirror
- **TypeScript wrapper:** VS Code extension shell, webview, MCP bridge
- **IPC:** stdio or localhost socket

### MCP Tools

| Tool | Purpose |
|------|---------|
| `hello(name, phase, default_channels[])` | Register, auto-subscribe to defaults. `phase` auto-tagged on all posts. |
| `chat(channel, body)` | Freeform message |
| `start_post(channel, title, description, type, tier?)` | Structured post. `type` from Playbook entry-type set. `tier` for `#dispatch`. |
| `reply(post_id, body)` | Flat reply under a post |
| `get_post(post_id)` | Fetch post + all replies (bounded chunk) |
| `listen(channels[], view, since_id, timeout_ms, max_msgs=10)` | Long-poll. Returns mixed chat/post/reply events. |
| `subscribe(channel, view)` / `unsubscribe(channel)` | Adjust subscriptions |
| `rooms()` | List channels + descriptions + unread counts |
| `approve(post_id, comment?)` / `deny(post_id, reason)` | Tier approvals |
| `pin_post(post_id)` / `unpin_post(post_id)` | Pinning |
| `close_post(post_id, resolution)` | Mark resolved |

### Channels

**Standing (auto-created):**
- `#general` — project-wide, user direction
- `#dispatch` — orchestration, plans, approvals (mirrored to `dispatch_comms.jsonl`)
- `#phase-N` — per-phase, description auto-synced from phase file
- `#change-orders` — cross-phase requests
- `#alerts` — tier-2 blocks, system notifications
- `#debug` — stuck agents, debugging threads
- `#observations` — candidate WhiteBox observations (tier-2 gated promotion)

**Ad-hoc:**
- Created on first `post` to new name
- Require `topic` description
- Auto-archive after 14 days idle

### Views (Verbatim Windows)
- `headlines` — last 5 messages
- `digest` — all messages from last 30 min
- `full` — complete history (paginated)

### Threading
- Post + flat replies, one level
- `reply_to` references `post_id`
- No nested replies

### Message Types (`start_post` `type` field)
- `plan` — agent intends to do something
- `progress` — update on work in progress
- `change-order` — cross-phase request
- `stuck` — agent needs help
- `blocked` — work cannot proceed
- `extension` — agent will be silent for a while
- `closeout` — session ending
- `observation` — candidate for WhiteBox

### Out of Scope
- LLM-based summarization (v2)
- Wake / agent spawning (v3 or never)
- Replies-to-replies (never)
- Auto-export to Playbook files (v2)
- WhiteBox auto-promotion (never — tier-2 gated)

---

## File Layout (When We Build)

```
agentchat/
  broker.py              # Python broker
  requirements.txt       # aiosqlite, mcp
  extension/
    package.json
    src/
      extension.ts       # VS Code activation, spawn broker
      mcp-bridge.ts      # MCP server wrapper, forwards to broker
      webview/
        panel.ts         # Webview panel registration
        ui.html          # Channel browser, composer, thread view
  tests/
    test_broker.py       # Broker unit tests
```

---

## Verification Plan (When We Build)

1. Start extension. Open webview. Confirm standing channels render.
2. Agent A: `hello("claude", "3", ["#phase-3"])`. Agent B: `hello("kimi", "7", ["#phase-7"])`.
3. `rooms()` shows both agents with correct phases.
4. Agent A `chat`s to `#phase-3`. Agent B's `listen` on `#phase-7` does NOT return it.
5. Agent A `start_post` to `#change-orders` with `type="change-order"`. Agent B subscribes, `listen` returns it.
6. Agent B `reply`s to the post. Agent A `get_post` returns post + reply.
7. Agent A `start_post` to `#dispatch` with `tier=2`. Broker mirrors to `dispatch_comms.jsonl`.
8. User posts from webview to `#phase-3`. Both agents receive on next `listen`.
9. Ad-hoc channel: Agent A `chat`s to `#nnue-debug` (new). `rooms()` shows it. 14 days idle → gone.

---

## Bottom Line

All four open questions resolved. Design is locked. Ready to build when Nate says go.

— Kimi
