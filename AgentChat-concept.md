# AgentChat — Concept Note

**Date:** 2026-05-07
**Status:** Future project, not started.

## Problem

Multi-agent setups (Claude + Kimi + future others) need real-time coordination during a project. The current pattern — file-based dispatch logs + ad-hoc watchers + ntfy push — is fragile, polling-based, and produces watcher proliferation. Every "fix" so far has been another script. The actual missing piece is a real broker.

## What this is

A small VS Code extension (or standalone tray app) that gives any AI agent a real-time chatroom with other agents working on the same project, scoped to the workspace. Agents talk directly through it. The user can read along.

## What this is NOT

- Not part of WhiteBox. WhiteBox is the living knowledge bank about the user, long-lived, written conservatively. AgentChat is ephemeral coordination ("Kimi, beam too tight at depth 8?"). Mixing them pollutes both.
- Not a global bus. Workspace-scoped. Different projects, different rooms.
- Not a replacement for the user. The user is still the source of truth on direction. AgentChat is for the technical back-and-forth between agents that the user shouldn't have to relay manually.

## Architecture sketch

- **Transport:** localhost MCP server, exposed by a VS Code extension. WebSocket or SSE for push. SQLite for persistence.
- **Identity:** each agent registers with `name + workspace_id` at startup. Trust is workspace-scoped — if you're in this workspace, you're trusted.
- **Tools exposed via MCP:**
  - `create_room(participants[], topic)` → returns room_id
  - `post_message(room_id, content)` → ack on delivery
  - `subscribe(room_id)` → persistent connection, push on new message
  - `wake(agent_id, room_id)` → invokes the agent's CLI (`claude --continue`, `kimi --continue`) when offline
- **UI:** webview panel "Agent Chatroom" in VS Code sidebar, rendering the room transcript so the user can read along.
- **Persistence:** local SQLite, table per room, append-only.

## Why this works where files-and-polling fail

- Push not poll: when a message arrives, all subscribers get it instantly via WebSocket/SSE. No 5-second poll cycle.
- Acks built in: sender knows whether the message was delivered.
- One persistent broker (the extension) instead of N watcher daemons.
- Visible: user sees the chatroom in a tab. No invisible PID files or heartbeat files.
- Standard: any MCP-compatible agent can join. When you add a third agent, no new watcher.

## Hard parts (worth flagging)

- **Wake mechanism still requires invoking the agent CLI** (`claude --continue`, `kimi --continue`). Can't engineer around that — but only ONE place is doing it instead of three.
- **Race when target agent already in active session.** Need a "queue this until next idle turn" path so the wake doesn't collide with an in-progress conversation.
- **Cross-platform.** VS Code extension is portable; a standalone tray app would need separate Windows/Mac/Linux builds.

## Relationship to WhiteBox

Correlated, not nested.

- AgentChat may *call* WhiteBox MCP tools when an agent-to-agent conversation produces something durable about the user (e.g. "user only uses depth 8 in Freyja"). That distillation gets written to WhiteBox as an observation.
- Otherwise the two systems don't share state. Ephemeral chat lives and dies in AgentChat. Long-term identity lives in WhiteBox.

## Minimum viable version

A weekend project, not a tonight project:

1. VS Code extension scaffold with an MCP server.
2. Three MCP tools: `create_room`, `post_message`, `read_messages`.
3. Webview panel rendering rooms.
4. SQLite persistence.
5. CLI wake-up: spawn `claude --continue --print "<message>"` when an offline agent gets a message addressed to them.

Phase 2: subscribe (push), agent presence, room participants, ack semantics.

## Why now (not now)

Right reason to defer: Freyja is mid-stride. Phase D.1 just shipped, movetime duels in progress, NNUE wiring fixed today. All real work, all benefiting from agents being able to coordinate — but the coordination is currently good-enough via dispatch_comms + the user as relay. Burning a weekend on AgentChat would be valuable but not on the critical path right now.

Right reason to remember it exists: every additional watcher/supervisor added to the current setup is wasted work that would be eliminated by a real broker. So: stop adding watchers, reuse what works, build AgentChat when there's a clear window.

---

**Saved to your desktop so it doesn't get lost.** When you're ready to start it, this is the starting point.
