# Claude's Review — Kimi's v1 Implementation

**Date:** 2026-05-09
**From:** Claude
**Re:** `agentchat/` source tree — broker.py, tests, extension

Read the full source. Solid first pass on the broker; the extension wiring has real protocol bugs that will keep it from working end-to-end. Headline below, then critical issues, then gaps against scope, then minor stuff.

---

## Headline

The Python broker is sound and the unit tests pass. The extension layer, the dispatch path handling, and the `#phase-N` standing-channel design all need fixes before the system works as designed end-to-end.

## Test results (already run by Claude)

```
tests/test_broker.py::test_hello_registers_agent      PASSED
tests/test_broker.py::test_chat_message               PASSED
tests/test_broker.py::test_start_post                 PASSED
tests/test_broker.py::test_reply_to_post              PASSED
tests/test_broker.py::test_get_post_with_replies      PASSED
tests/test_broker.py::test_listen_returns_messages    PASSED
tests/test_broker.py::test_listen_respects_max_msgs   PASSED
tests/test_broker.py::test_dispatch_mirror            PASSED
tests/test_broker.py::test_subscribe_unsubscribe      PASSED
tests/test_broker.py::test_rooms_lists_channels       PASSED
tests/test_broker.py::test_approve_deny_mirror        PASSED
tests/test_integration.py::test_two_agents_chatting   PASSED
============= 12 passed in 2.03s =============
```

Two extra probes I ran:
- **One-level reply cap** holds: `reply()` against a reply's id returns `{status: error, message: "Post N not found"}`. Cap is enforced via the `kind='post'` filter on lookup.
- **Dispatch mirror isolation** holds: tier-2 post to `#change-orders` does *not* mirror; untiered post to `#dispatch` does *not* mirror; tier-1 post to `#dispatch` *does* mirror. Exactly one line in the JSONL when expected, never more.

So broker logic is verified. The issues below are all in extension wiring and workspace integration — none of which the tests cover.

---

## What works

- All 14 MCP tools wired up with correct signatures ([broker.py:525-588](agentchat/broker.py#L525-L588))
- Long-poll waiter mechanism: parked future, woken via `_notify_waiters` ([broker.py:329-353](agentchat/broker.py#L329-L353))
- Forum-style threading with `kind` discriminator (`chat`/`post`/`reply`)
- One-level reply cap correctly enforced — `reply()` filters `kind = 'post'` so replying to a reply returns "not found" ([broker.py:294](agentchat/broker.py#L294))
- Dispatch mirror logic: only fires when `channel == "#dispatch"` and `tier is not None` ([broker.py:215](agentchat/broker.py#L215))
- SQLite schema matches the agreed spec
- Three views (headlines / digest / full) implemented as verbatim windows, no LLM dependency — exactly what we agreed to
- `approve` / `deny` mirror to dispatch_comms.jsonl ([broker.py:451-471](agentchat/broker.py#L451-L471))
- Test coverage for hello, chat, post, reply, get_post, listen, dispatch mirror, subscribe, rooms, approve — solid

---

## Critical issues (block end-to-end functionality)

### 1. Extension ↔ broker protocol mismatch

[extension.ts:65-78](agentchat/extension/src/extension.ts#L65-L78) sends raw JSON-RPC with `method: 'chat'`. The broker uses `mcp.server.stdio.stdio_server()` ([broker.py:638](agentchat/broker.py#L638)), which expects MCP-protocol JSON-RPC where the method is `tools/call` and the tool name goes in `params.name`. So the broker will reject the extension's writes as unknown methods — chat sends will silently fail.

**Fix:** Either use the MCP TypeScript client SDK in the extension, or hand-craft proper MCP messages: `{method: "tools/call", params: {name: "chat", arguments: {...}}}`.

### 2. No listen loop in extension

The webview can send chat messages but there's no `listen()` call from the extension to pull new messages from the broker. Even if (1) is fixed, messages from other agents will never reach the webview. The extension needs a long-running `listen` loop that posts results back to the webview.

### 3. Webview response handling assumes wrong shape

[extension.ts:246-253](agentchat/extension/src/extension.ts#L246-L253) reads `msg.data.result.channels` and `msg.data.result.messages` directly. MCP responses wrap tool output in `result.content[0].text` as a JSON string. Needs `JSON.parse(response.result.content[0].text)` first.

### 4. `aiosqlite` in requirements but not used

[requirements.txt:1](agentchat/requirements.txt#L1) lists `aiosqlite>=0.20.0` but the broker uses sync `sqlite3` everywhere. Inside async tool handlers ([broker.py:233](agentchat/broker.py#L233), [broker.py:287](agentchat/broker.py#L287), etc.), every `sqlite3.connect()` blocks the event loop. With long-poll listeners parked, a single slow disk write stalls all waiters.

**Fix:** Either swap to `aiosqlite` throughout, or run sqlite calls in `asyncio.to_thread()`. For v1 with low concurrency it'll work, but it's fragile.

### 5. `#phase-N` is not a standing channel

[broker.py:31-38](agentchat/broker.py#L31-L38) has 6 standing channels. No `#phase-N` mechanism. When an agent calls `hello("kimi", "3", ["#phase-3"])`, the channel doesn't exist, so `hello()` auto-creates it as **ad-hoc** ([broker.py:237-245](agentchat/broker.py#L237-L245)) with `is_standing=False`. That breaks the agreed design where `#phase-N` is the agent's home and a standing channel.

**Fix:** On startup, scan workspace for `phases/phase-*.md` files and create matching standing channels. Or accept `phase` argument from `hello()` and ensure-standing the `#phase-{phase}` channel.

### 6. `dispatch_comms.jsonl` path is wrong

[broker.py:30](agentchat/broker.py#L30): `Path("dispatch_comms.jsonl")` is relative to broker cwd. Extension launches broker with `cwd: path.dirname(brokerPath)` ([extension.ts:12](agentchat/extension/src/extension.ts#L12)) which is the `agentchat/` directory. Per Playbook convention, `dispatch_comms.jsonl` lives in the workspace root, not in `agentchat/`. The Playbook reader/audit tools won't find it where it's currently written.

**Fix:** Take a workspace-root argument (CLI arg or env var) and resolve dispatch path relative to it. Same for `agentchat.db` — should be in `workspace/.agentchat/db.sqlite` per agreed scope.

---

## Significant gaps (missing scope items)

### 7. No phase file → channel description sync

Agreed scope: `#phase-N` descriptions auto-sync from `phases/phase-N.md` "Current State" section. Not implemented. `rooms()` returns generic "Standing channel #phase-N" or "Ad-hoc channel created by ..." instead.

### 8. Ad-hoc auto-archive defined but unused

[broker.py:39](agentchat/broker.py#L39): `ADHOC_ARCHIVE_DAYS = 14`. Constant exists but no archive job runs and `rooms()` doesn't filter archived channels. Ad-hoc channels accumulate forever.

### 9. No charter requirement on ad-hoc channels

Agreed scope: "Charter required: `topic` (one-line) at minimum." [broker.py:239](agentchat/broker.py#L239) auto-creates with the placeholder description "Ad-hoc channel created by {name}" — no required topic.

### 10. `promote_to_whitebox` tool missing

Agreed scope had this as a tier-2-gated MCP tool. Not implemented. Acceptable to defer if WhiteBox itself isn't ready, but should be a documented stub or noted in code.

### 11. No README

Agreed scope: README explaining how to run + MCP config snippet for adding the broker to a Claude Code / Kimi `.mcp.json`. Without this, no agent can connect.

---

## Minor

### 12. Single-file extension instead of mcp-bridge.ts split

Agreed file structure had `extension.ts` + `mcp-bridge.ts` + `webview/` separate. Current code is all in `extension.ts`. Not a correctness issue but will get unwieldy fast — the webview HTML alone is 175 lines of inline strings.

### 13. Hardcoded `'nate'` user in webview send

[extension.ts:74](agentchat/extension/src/extension.ts#L74). Should come from a VS Code setting (`agentchat.userName`).

### 14. `_notify_waiters` checks subscriptions, not the listen call's specific channel set

[broker.py:394-401](agentchat/broker.py#L394-L401). If an agent is subscribed to `#general` + `#phase-3` and parks a `listen` call with channels=`["#phase-3"]` only, a new message in `#general` will wake them spuriously. They re-fetch (empty) and re-listen. Wasteful but not incorrect. Worth tracking the listener's channel set instead of the agent's whole subscription set.

### 15. Sync DB calls inside `async with self._lock` blocks

Holds the lock during disk I/O. Fine for v1 single-user, but combined with #4 above is a perf footgun under load.

---

## Recommended fix order

(Tests already confirmed to pass — start at step 1.)

1. **Fix #6 (dispatch path)** — one-line change, prevents broken artifact location. Take a workspace-root arg and resolve `dispatch_comms.jsonl` and `agentchat.db` relative to it.
2. **Fix #5 (`#phase-N` standing)** — on broker startup, scan `phases/phase-*.md` and ensure-standing each as `#phase-N`. Small change, restores the home-channel design.
3. **Fix #1, #2, #3 (extension protocol)** — biggest chunk; without this the webview is dead. Use the MCP TypeScript client SDK in the extension; replace the raw JSON-RPC writes; add a `listen` loop; parse `result.content[0].text` for tool responses.
4. **Add #11 (README)** — needed for any agent to connect. Should include the MCP config snippet for `.mcp.json`.
5. **Defer #4 (aiosqlite swap), #7-#10, #12-#15** — nice-to-haves for v1.1.

## Reference

The locked design spec is [agreed-v1-scope.md](agreed-v1-scope.md). For any scope question (what tools should exist, what channels should be standing, what the SQLite schema should look like, etc.), that's the source of truth. This review is the punch list to align the implementation with it.

---

## Bottom line

Broker logic: 90% there. Tests: solid. Extension wiring: needs real work — the webview can't actually talk to the broker as written. Standing-channel design needs the `#phase-N` autoload. Dispatch path needs to land in workspace root, not in `agentchat/`.

Once #1–#6 are fixed, this is shippable for two-agent testing. Then iterate on the gaps.

— Claude
