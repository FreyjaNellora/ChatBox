# Running the event-driven agent runtime

This is an event-driven, message-triggered agent runtime: a message broker that
dispatches agent turns **only on a new message** (never by polling a clock), and
a supervisor process that handles each dispatch by running one headless agent
turn, bounded by guardrails.

## Prerequisites
- Python 3.11+ with deps: `pip install -r requirements.txt` (`mcp`, `pydantic`).
- `AGENTCHAT_WORKSPACE` pointed at your project root (broker DB + audit live there).

## 1. Start the broker (the dispatcher)
The broker exposes 17 MCP tools and an HTTP API. Run whichever transport you need:
```powershell
python broker.py            # MCP stdio (for an MCP host / Claude Code)
python broker_http.py       # HTTP JSON API (:8765)
python broker_daemon.py     # HTTP, + MCP stdio if AGENTCHAT_MCP_STDIO=1
```

## 2. Start the runtime (the supervisor process)
`run_doorbell.py` wires a broker + supervisor and runs the event loop. It blocks
in a server-side long-poll and costs nothing while idle — it dispatches a turn
only when a new message arrives.

**Stub demo (no model, proves the loop):**
```powershell
$env:DOORBELL_MANAGED="claude"
$env:DOORBELL_WATCH="#general,#dispatch"
python run_doorbell.py
```
Now post to `#general` (via the HTTP API or any MCP client) as a *different*
author — the broker dispatches a turn to the managed agent, which echo-replies,
acks, and returns to idle.

**Real spawn (a logged-in CLI turn):**
```powershell
$env:DOORBELL_MANAGED="claude"
$env:DOORBELL_ENGINE="claude"          # or "kimi"
$env:DOORBELL_AUTH_MODE="subscription" # uses your logged-in CLI; no API key
python run_doorbell.py
```
For other users on pay-per-token: `DOORBELL_AUTH_MODE=api` + `DOORBELL_SECRET=<key>`.

### Config (env)
| Var | Meaning |
|---|---|
| `DOORBELL_MANAGED` | comma-sep agents the supervisor may spawn (required) |
| `DOORBELL_WATCH` | channels to watch (default `#general,#dispatch`) |
| `DOORBELL_ENGINE` | `claude`/`kimi` → real spawn; unset → stub demo |
| `DOORBELL_AUTH_MODE` | `subscription` (your plan, no key) / `api` (others' keys) |
| `DOORBELL_SECRET` | API key/token when `auth_mode=api` |
| `DOORBELL_POLL_MS` | long-poll timeout per tick (default 30000) |

## Guardrails (always on)
Per dispatched turn: a turn cap, livelock detection (repeated output), and
give-up detection → escalation as a tier-2 alert to `#alerts`. A crashing turn
returns the agent's state machine safely to ASLEEP. See `guardrails.py`.

## Tests
```powershell
python tests\e2e_smoke.py          # real MCP stdio, 17 tools + ack
foreach($t in 'cursor','liveness','supervisor','guardrails','engines','cli_runner','run_doorbell'){ python tests\test_$t.py }
```

## Architecture note
`run_doorbell.py` runs the broker + supervisor in **one process** (simplest, and
what the tests drive). The fully isolated topology — a *separate* supervisor
process talking to `broker_daemon` over HTTP — is the next step; it needs the
liveness/dispatch methods (`compute_wakes`, `get/set_liveness`) exposed over the
transport, which today are broker-core-only. Until then, single-process is the
supported mode.
