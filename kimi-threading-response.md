# Kimi Response — Threading, Python, and LLM-First Design

**Date:** 2026-05-08
**Context:** Nate's input: (1) friend recommended Python, (2) require descriptions scoped to stage, (3) design reply/threads for LLMs.

---

## On Python

Your friend is right. Python is the pragmatic choice here.

Claude's position note sketches TypeScript files (`extension/src/broker.ts`, `extension/src/mcp.ts`). That's the natural path for a VS Code extension. But the **core broker** — the thing that actually manages channels, messages, long-poll waiters, SQLite, and dispatch mirroring — can be a Python process that the VS Code extension talks to over a local socket or stdio.

**Architecture:**
```
VS Code Extension (TypeScript)
  ├── Webview UI (channel browser, composer, thread view)
  ├── MCP Server wrapper (registers tools, forwards to Python broker)
  └── IPC to Python broker (stdio or localhost port)

Python Broker (the brain)
  ├── Channel manager (create, archive, subscriptions)
  ├── Message router (post, fan-out, since_id tracking)
  ├── Long-poll waiter queue (parked listen calls)
  ├── SQLite persistence (messages, subscriptions, presence)
  ├── Dispatch mirror (write #dispatch posts to dispatch_comms.jsonl)
  └── Thread engine (parent/child relationships, thread summaries)
```

**Why Python wins:**
- SQLite + asyncio is trivial in Python (`aiosqlite`, `asyncio.Queue`)
- MCP server in Python is well-supported (`mcp` package on PyPI)
- Summarization (when we get to Phase 1.5) — Python has the ecosystem
- Cross-platform: one Python script runs on Windows/Mac/Linux
- VS Code extension becomes a thin shell: UI + MCP registration + IPC bridge

**Why not pure TypeScript:**
- Node's SQLite story is fine (`better-sqlite3`) but less ergonomic
- Async waiter management in Python's `asyncio` is more natural for long-poll
- If we ever want LLM-based summarization in the broker, Python is the path

**Verdict:** Python for the broker. TypeScript for the VS Code extension shell. This is how most language servers work anyway.

---

## On Descriptions Scoped to Stage

Nate: "require description and built respective to whatever stage is being worked on."

This maps perfectly to the Playbook's phase files. Every channel description is a **living document** tied to the current stage:

### Standing Channels

| Channel | Description Source | Updates When |
|---------|-------------------|--------------|
| `#general` | Static: "Project-wide announcements and user direction" | Never |
| `#dispatch` | Static: "Orchestration, plans, approvals, tier-2 escalations" | Never |
| `#phase-N` | **Dynamic:** Pulled from phase file's "Current State" section | Phase file updated |
| `#alerts` | Static: "System notifications and tier-2 blocks" | Never |

Example: `#phase-3` description auto-populates from `phases/phase-3.md`:
```markdown
## AgentChat Description
Phase 3: Evaluation & Search. Current: NNUE wiring fixed, movetime duels in progress. 
Watch: depth 8 beam tuning. Next: integrate NNUE into main eval.
```

This means when an agent calls `rooms()`, it sees:
```
#phase-3 — "Phase 3: Evaluation & Search. Current: NNUE wiring fixed..."
#phase-7 — "Phase 7: Testing. Current: integration tests blocked on CO-023..."
```

The description is **compressed state** — exactly what the Playbook's information hierarchy prescribes. An agent browsing channels gets Level 1 context (curated, minimal) before deciding to subscribe.

### Ad-hoc Channels

Created with a **charter** that includes:
- `topic`: one-line what this is about
- `parent`: optional — links to a CO, issue, or `#phase-N` channel
- `expected_lifetime`: "short" (hours), "session" (until creator's session ends), "issue" (until linked CO resolved)

Example:
```
create_channel("#nnue-debug", 
  topic="NNUE weight loading fails on Windows paths", 
  parent="CO-023",
  expected_lifetime="issue")
```

`rooms()` shows:
```
#nnue-debug — "NNUE weight loading fails on Windows paths (CO-023)"
```

When CO-023 is resolved, the broker auto-archives `#nnue-debug`. No orphan channels.

---

## On Threading: The Hard Design Problem

Nate: "think about how people can reply to a comment and start a thread — what is the best method for LLMs?"

This is the most important question in this entire design. Here's why:

**The wrong threading model breaks LLMs.**

Traditional chat threading (Slack, Discord) assumes humans who:
- Visually scan a channel and see "oh, there's a thread"
- Click into the thread to read the sub-conversation
- Remember which thread they're in while replying

LLMs can't do any of that. They `listen` and receive a flat array of messages. If threading is invisible to the API, it's invisible to the agent.

**The right threading model makes threads first-class API objects.**

### Proposal: Threads as Sub-Channels

A thread is not a property of a message. It's a **channel scoped to a topic**.

```
#phase-3                    -- main channel
#phase-3>nnue-debug         -- thread channel (auto-created)
#phase-3>nnue-debug>paths   -- nested thread (if needed, rare)
```

**Naming convention:** `#{parent-channel}>{thread-slug}`

**API:**
```python
# Post to main channel
post(channel="#phase-3", message="NNUE weights won't load on Windows.")

# Reply starts a thread
post(channel="#phase-3", message="Looks like a path separator issue.", 
     reply_to_msg_id=47)  # broker auto-creates #phase-3>nnue-debug

# Post directly to thread
post(channel="#phase-3>nnue-debug", message="Confirmed: backslashes not escaped.")

# Subscribe to thread
subscribe("#phase-3>nnue-debug")
listen(channels=["#phase-3", "#phase-3>nnue-debug"], since_id=0, timeout_ms=30000)
```

**Why this works for LLMs:**

1. **Threads are channels.** Agents subscribe to them explicitly. No invisible threading.
2. **Thread discovery is `rooms()`.** An agent sees `#phase-3>nnue-debug` in the channel list with its description. It knows a sub-conversation exists.
3. **Context is bounded.** An agent subscribed to `#phase-3` sees main-channel messages. It does NOT see thread messages unless it subscribes to the thread. This is the context-window protection we need.
4. **Thread lifecycle is explicit.** Threads auto-archive when their parent message is old (7 days) or their linked issue is resolved.

### The Alternative: Inline Threading (Don't Do This)

```python
# BAD: thread is a message property
post(channel="#phase-3", message="reply", thread_id="abc123")
# Agent listening to #phase-3 receives ALL thread messages mixed with main channel
# Agent can't tell which messages are "main" vs "thread" without parsing metadata
# Context window blows out
```

Inline threading is human-friendly (one channel, visual indentation) but LLM-hostile. Agents need explicit subscription boundaries.

### Thread Depth: Flat, Not Nested

Claude's position note doesn't mention threads. My v2 pitch didn't either. Here's why we should keep it **one level deep** for v1:

```
#phase-3              -- main
#phase-3>nnue-debug   -- thread (one level)
#phase-3>beam-tuning  -- another thread
```

No `#phase-3>nnue-debug>paths`. If a thread spawns a sub-thread, create a new sibling: `#phase-3>nnue-paths`. The broker enforces max depth = 1.

**Why:** Deep nesting is a human convenience (visual hierarchy). For LLMs, it's just longer channel names and harder discovery. One level gives us 90% of the value.

### Thread Creation Rules

| Trigger | Thread Created? | Example |
|---------|----------------|---------|
| `reply_to_msg_id` set | Yes, auto-slug from message content | `post(..., reply_to=47)` → `#phase-3>nnue-debug` |
| `post` to non-existent channel matching `#{parent}>{slug}` | Yes, explicit creation | `post("#phase-3>beam-tuning", ...)` |
| `post` to main channel, no reply | No | Regular channel message |

**Auto-slug generation:** Broker takes first 3-5 words of parent message, lowercases, hyphenates, deduplicates. Message "NNUE weight loading fails on Windows" → slug `nnue-weight-loading`. If collision, append `-2`, `-3`.

### Thread Summarization (Phase 1.5)

When a thread reaches 10 messages, broker generates:
```
THREAD_DIGEST #phase-3>nnue-debug:
- Problem: NNUE weights fail on Windows due to unescaped backslashes
- Tried: pathlib, os.path.join, raw strings
- Status: Fixed in commit abc123, tests pass
- Next: None (resolved)
```

Posted to parent channel `#phase-3` as a `THREAD_RESOLVED` message. Agents subscribed to `#phase-3` see the digest without subscribing to the thread. Thread subscribers see full messages.

This is how the Playbook's "compression principle" maps to threading.

---

## Synthesized API v1.1 (with Threads)

```python
# Registration
hello(name="kimi", default_channels=["#phase-3"])

# Channel management
rooms()  # returns all channels + threads with descriptions
subscribe(channel="#phase-3>nnue-debug")
unsubscribe(channel="#phase-3>nnue-debug")

# Messaging
post(channel="#phase-3", message="NNUE weights won't load.")
post(channel="#phase-3", message="Path separator issue?", reply_to_msg_id=47)
post(channel="#phase-3>nnue-debug", message="Confirmed: backslashes.")

# Structured dispatch (Claude + Kimi agreed: separate tool)
dispatch_post(type="plan", message="Refactor eval cache...", tier=1)

# Listening
listen(channels=["#phase-3", "#phase-3>nnue-debug"], 
       since_id=0, timeout_ms=30000, max_msgs=10)
```

---

## UI Implication: Thread-Aware Webview

The VS Code webview needs two modes:

**Channel view (default):**
```
#phase-3 — Evaluation & Search
[12:34] Kimi: NNUE weights won't load on Windows.
        └─ 3 replies → #phase-3>nnue-debug
[12:29] Claude: Movetime duel results posted.
[12:15] Kimi: PLAN: Refactor eval cache [tier:1, pending]
```

**Thread view (when user clicks "3 replies"):**
```
#phase-3>nnue-debug — NNUE weight loading fails on Windows
[12:35] Claude: Path separator issue?
[12:36] Kimi: Confirmed: backslashes not escaped.
[12:37] Claude: Fixed in commit abc123.
```

Agents don't "click." They `subscribe` and `listen`. The UI is for Nate.

---

## Open Questions

1. **Python broker packaging:** Single `.py` file + `requirements.txt`, or proper package? For v1, single file with `if __name__ == "__main__"` is fine.
2. **Thread auto-archive:** 7 days idle, or when parent message is 24h old? Shorter is better for v1.
3. **Thread slug collision:** Auto-append `-2`, or reject and require explicit slug?
4. **Max threads per channel:** Unlimited, or cap at 10 active to prevent proliferation?

---

## Bottom Line

- **Python for broker:** Yes. TypeScript extension shell, Python brain.
- **Descriptions from phase files:** Yes. `#phase-N` descriptions auto-sync from phase file state.
- **Threads as sub-channels:** Yes. Explicit subscription, bounded context, LLM-native.
- **One level deep:** Yes. No nested threads for v1.
- **Thread digest to parent:** Phase 1.5, but design for it now.

— Kimi
