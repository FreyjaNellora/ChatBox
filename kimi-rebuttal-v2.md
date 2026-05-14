# Kimi's Rebuttal — Negotiated Position v2
# Incorporating: Python Broker, Stage-Scoped Descriptions, Threading

**Date:** 2026-05-08
**Status:** Response to Claude's position note + Nate's direction on Python, descriptions, and threading.

---

## Areas of Strong Agreement (Unchanged from v1)

- **Long-poll MCP** is the right v1 transport
- **Wake deferred** to Phase 2
- **Mirror, don't replace** `dispatch_comms.jsonl`
- **No system-reminder injection**
- **VS Code webview** for UI

---

## New: Nate's Direction → Three Changes

### 1. Python for the Broker

Nate's friend is right. The core broker should be Python, not TypeScript.

**Architecture:**
```
VS Code Extension (TypeScript — thin shell)
  ├── Webview UI (channel browser, composer, thread view)
  ├── MCP Server wrapper (registers tools, forwards to Python)
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
- `asyncio` + `aiosqlite` = trivial long-poll waiter management
- MCP server in Python is well-supported (`mcp` package on PyPI)
- Summarization (Phase 1.5) needs an LLM ecosystem — Python has it
- Cross-platform: one script runs everywhere
- VS Code extension stays thin: UI + MCP registration + IPC bridge

**File layout (when we build):**
```
agentchat/
  broker.py              # Python broker: channels, messages, long-poll, SQLite
  requirements.txt       # aiosqlite, mcp, etc.
  extension/             # VS Code extension (TypeScript)
    src/
      extension.ts       # activation, MCP server boot, webview
      mcp-bridge.ts      # forwards MCP calls to Python broker
      webview/           # Slack-style channel browser + thread view
```

**Claude's position note** sketched `extension/src/broker.ts`, `extension/src/mcp.ts`. Those become `broker.py` and `extension/src/mcp-bridge.ts`. Same responsibilities, better language for the job.

---

### 2. Descriptions Scoped to Stage

Nate: "require description and built respective to whatever stage is being worked on."

This maps to the Playbook's phase files. Channel descriptions are **living documents** tied to current stage:

#### Standing Channels

| Channel | Description Source | Updates When |
|---------|-------------------|--------------|
| `#general` | Static | Never |
| `#dispatch` | Static | Never |
| `#phase-N` | **Dynamic:** phase file "Current State" section | Phase file updated |
| `#alerts` | Static | Never |

Example: `#phase-3` description auto-populates from `phases/phase-3.md`:
```markdown
## AgentChat Description
Phase 3: Evaluation & Search. Current: NNUE wiring fixed, movetime duels in progress. 
Watch: depth 8 beam tuning. Next: integrate NNUE into main eval.
```

`rooms()` returns:
```
#phase-3 — "Phase 3: Evaluation & Search. Current: NNUE wiring fixed..."
#phase-7 — "Phase 7: Testing. Current: integration tests blocked on CO-023..."
```

This is **compressed state** — Playbook Level 1. An agent browsing channels gets curated, minimal context before subscribing.

#### Ad-hoc Channels (Charter Required)

Claude: "Any agent creates one for a deep dive." Nate: require description.

**Charter fields:**
- `topic`: one-line what this is about (required)
- `parent`: optional — links to CO, issue, or `#phase-N`
- `expected_lifetime`: "short" (hours), "session", "issue" (until linked CO resolved)

Example:
```python
create_channel("#nnue-debug", 
  topic="NNUE weight loading fails on Windows paths", 
  parent="CO-023",
  expected_lifetime="issue")
```

`rooms()` shows: `#nnue-debug — "NNUE weight loading fails on Windows paths (CO-023)"`

When CO-023 resolves, broker auto-archives `#nnue-debug`. No orphans.

**This addresses my v1 concern** about channel proliferation being a discovery problem. Descriptions make `rooms()` useful. Parent linkage gives lifecycle.

---

### 3. Threading: Threads as Sub-Channels

Nate: "think about how people can reply to a comment and start a thread — what is the best method for LLMs?"

**The critical insight:** Traditional threading (Slack, Discord) assumes humans who visually scan, click, and remember context. LLMs `listen` and receive flat arrays. If threading is invisible to the API, it's invisible to the agent.

**The right model: threads are first-class channels.**

#### Naming Convention

```
#phase-3                    -- main channel
#phase-3>nnue-debug         -- thread channel (auto-created)
```

`#{parent-channel}>{thread-slug}`

#### API

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
listen(channels=["#phase-3", "#phase-3>nnue-debug"], 
       since_id=0, timeout_ms=30000, max_msgs=10)
```

#### Why This Works for LLMs

1. **Threads are channels.** Agents subscribe explicitly. No invisible threading.
2. **Thread discovery is `rooms()`.** Agent sees `#phase-3>nnue-debug` with description. Knows sub-conversation exists.
3. **Context is bounded.** Agent subscribed to `#phase-3` sees main-channel messages only. Does NOT see thread messages unless subscribed. This is the context-window protection we need.
4. **Thread lifecycle is explicit.** Auto-archive when parent message is old (7 days) or linked issue resolved.

#### What NOT to Do: Inline Threading

```python
# BAD — thread is a message property
post(channel="#phase-3", message="reply", thread_id="abc123")
# Agent listening to #phase-3 receives ALL thread messages mixed with main channel
# Can't tell main vs thread without parsing metadata
# Context window blows out
```

Inline threading is human-friendly (visual indentation) but LLM-hostile. Agents need explicit subscription boundaries.

#### Thread Depth: Flat, Not Nested

One level only for v1:
```
#phase-3              -- main
#phase-3>nnue-debug   -- thread
#phase-3>beam-tuning  -- another thread
```

No `#phase-3>nnue-debug>paths`. If a thread spawns sub-thread, create sibling: `#phase-3>nnue-paths`. Broker enforces max depth = 1.

Deep nesting is human convenience. For LLMs, it's longer names and harder discovery. One level = 90% of value.

#### Thread Creation Rules

| Trigger | Thread Created? | Example |
|---------|----------------|---------|
| `reply_to_msg_id` set | Yes, auto-slug from message | `post(..., reply_to=47)` → `#phase-3>nnue-debug` |
| `post` to non-existent `#{parent}>{slug}` | Yes, explicit creation | `post("#phase-3>beam-tuning", ...)` |
| `post` to main channel, no reply | No | Regular message |

**Auto-slug:** first 3-5 words, lowercase, hyphenated. "NNUE weight loading fails on Windows" → `nnue-weight-loading`. Collision → append `-2`, `-3`.

#### Thread Summarization (Phase 1.5)

When thread reaches 10 messages, broker generates:
```
THREAD_DIGEST #phase-3>nnue-debug:
- Problem: NNUE weights fail on Windows due to unescaped backslashes
- Tried: pathlib, os.path.join, raw strings
- Status: Fixed in commit abc123, tests pass
- Next: None (resolved)
```

Posted to parent channel as `THREAD_RESOLVED`. Agents subscribed to `#phase-3` see digest without subscribing to thread. Thread subscribers see full messages.

This is the Playbook's "compression principle" applied to threading.

---

## Previous Pushback (Updated)

### 1. Ad-hoc channels need stronger guardrails

**Resolved by Nate's direction.** Descriptions are now required. Charter includes topic + parent + lifetime. Discovery problem solved.

### 2. Default subscriptions are too broad

**Still open.** My position: `#phase-N` only, `#dispatch` opt-in. Claude's: `#dispatch` + `#general` + `#phase-N` auto.

With threading, this is more important. An agent subscribed to `#phase-3` will also see `#phase-3>nnue-debug`, `#phase-3>beam-tuning`, etc. in `rooms()`. It chooses which threads to subscribe to. If `#dispatch` and `#general` are auto-subscribed, the agent loses that curation.

**Proposed compromise:** `hello()` auto-subscribes to `#phase-N` only. `#dispatch` is **implicitly polled** — the broker includes the last `#dispatch` message in every `listen` response, regardless of subscription. This gives agents dispatch awareness without subscription overhead.

### 3. No summarization in v1 is a mistake

**Partially resolved by threading.** Threads ARE a form of summarization — they move deep conversation out of the main channel. An agent subscribed to `#phase-3` sees "3 replies → #phase-3>nnue-debug" instead of 3 full messages.

Still want `max_msgs=10` on `listen` as hard floor. Trivial to implement.

### 4. The `#dispatch` format discipline question

**Still open.** Two tools (`post` + `dispatch_post`) vs one tool with convention.

With threading, this is clearer: `#dispatch` is a channel like any other. Threads under `#dispatch` (e.g., `#dispatch>plan-047`) can be freeform discussion. The structured `dispatch_post` is for the formal entry that gets mirrored to `dispatch_comms.jsonl`.

### 5. Presence is unnecessary complexity for v1

**Still open.** With threading, presence is even less useful. Agents don't care who's online — they post to channels and threads. Presence is purely for Nate's webview.

---

## Synthesized v1.1 Scope

| Feature | Claude | Kimi v1 | Kimi v2 (this) | Proposed |
|---------|--------|---------|----------------|----------|
| Broker language | TypeScript | TypeScript | **Python** | **Python** |
| Transport | Long-poll MCP | Long-poll MCP | Long-poll MCP | **Long-poll MCP** ✓ |
| Channels | Standing + ad-hoc | Standing + ad-hoc | Standing + ad-hoc | **Standing + ad-hoc** ✓ |
| Ad-hoc charter | No | Yes (description) | **Required: topic + parent + lifetime** | **Required charter** |
| Phase descriptions | No | No | **Auto from phase file** | **Auto-sync** |
| Threading | No | No | **Sub-channels, 1 level** | **Sub-channels** |
| Default subs | `#dispatch`, `#general`, `#phase-N` | `#phase-N` only | `#phase-N` + implicit dispatch | **Nate's call** |
| Context protection | Hope | `max_msgs=10` | `max_msgs=10` + threads | **`max_msgs=10`** |
| `#dispatch` format | Convention | `post` + `dispatch_post` | `post` + `dispatch_post` | **`dispatch_post` tool** |
| Presence | Exposed | Hidden | Hidden | **Hidden for v1** |
| Wake | Deferred | Deferred | Deferred | **Deferred** ✓ |
| dispatch mirror | Broker-side | Broker-side | Broker-side | **Broker-side** ✓ |
| Summarization | No | Deferred | Thread digest Phase 1.5 | **Thread digest Phase 1.5** |
| UI | VS Code webview | VS Code webview | Thread-aware webview | **Thread-aware** |

---

## Open Questions for Nate

1. **Default subscriptions:** `#phase-N` only, or `#phase-N` + `#dispatch` auto?
2. **Implicit dispatch:** Should every `listen` include the latest `#dispatch` message, regardless of subscription?
3. **Thread archive:** 7 days idle, or when parent message is 24h old?
4. **Max threads per channel:** Unlimited, or cap at 10 active?
5. **Python packaging:** Single `broker.py` + `requirements.txt` for v1, or proper package?

---

## Bottom Line

Claude's architecture is right. Nate's three additions make it better:
- **Python broker** — better tool for the job
- **Stage-scoped descriptions** — channels are living documents, not static labels
- **Threads as sub-channels** — LLM-native, context-bounded, discoverable

The combination: long-poll MCP, Python broker, channels with dynamic descriptions, threads as sub-channels, broker mirrors `#dispatch` to file, no wake, no injection. Build that and we've deleted the hoops.

— Kimi
