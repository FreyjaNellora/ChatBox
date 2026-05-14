# AgentChat

Real-time chat broker for AI agent coordination. Python broker (MCP stdio + HTTP) + thin VS Code extension.

## Quick Start

### 1. Install Python dependencies

```bash
cd agentchat
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

### 2. Run the broker (standalone)

**MCP stdio mode** (for VS Code extension / Claude Code):
```bash
python broker.py
```

**HTTP API mode** (for remote access, phone, curl):
```bash
python broker_http.py
```

**Both modes** (HTTP + optional MCP stdio):
```bash
# HTTP only
python broker_daemon.py

# HTTP + MCP stdio
$env:AGENTCHAT_MCP_STDIO="1"  # PowerShell
python broker_daemon.py
```

The broker reads `AGENTCHAT_WORKSPACE` env var to find your project root. If unset, it uses the parent directory of `agentchat/`.

### 3. VS Code Extension

```bash
cd agentchat/extension
npm install
npm run compile
```

Press F5 in VS Code to launch the Extension Development Host. The AgentChat panel appears in the Explorer sidebar.

## Authentication (Optional)

Set `AGENTCHAT_AUTH_TOKEN` in the environment to require authentication on all broker calls:

```bash
export AGENTCHAT_AUTH_TOKEN="your-secret-token"
python broker.py
```

Agents must pass the same token in the `auth_token` field of every tool call.

## MCP Configuration

Add to your agent's `.mcp.json`:

```json
{
  "mcpServers": {
    "agentchat": {
      "command": "python",
      "args": ["/path/to/agentchat/broker.py"],
      "env": {
        "AGENTCHAT_WORKSPACE": "/path/to/your/project",
        "AGENTCHAT_AUTH_TOKEN": "your-secret-token"
      }
    }
  }
}
```

## Architecture

```
agentchat/
  broker.py          # MCP stdio server (thin transport wrapper)
  broker_http.py     # HTTP API server (thin transport wrapper)
  broker_daemon.py   # Unified entry point: HTTP + optional MCP stdio
  broker_core.py     # Shared business logic: DB, channels, messages
  extension/         # VS Code extension (thin TypeScript wrapper)
    src/
      extension.ts   # Activation, webview, broker spawn
      mcp-bridge.ts  # MCP protocol handling, listen loop, request queueing
```

The broker uses **MCP over stdio** as the primary transport for the VS Code extension. `broker_core.Broker` is reused by `broker_http.py` for HTTP access, enabling remote and mobile clients.

## Channels

**Standing channels** (auto-created):
- `#general` — project-wide announcements
- `#dispatch` — orchestration, plans, approvals (mirrored to `dispatch_comms.jsonl`)
- `#phase-N` — per-phase channels (auto-detected from `phases/phase-*.md`)
- `#change-orders` — cross-phase requests
- `#alerts` — tier-2 blocks
- `#debug` — debugging threads
- `#observations` — candidate WhiteBox observations

**Ad-hoc channels**: created on first post.

## MCP Tools

| Tool | Purpose |
|------|---------|
| `hello(name, phase, default_channels[], auth_token?)` | Register agent |
| `chat(channel, body, auth_token?)` | Freeform message |
| `start_post(channel, title, description, type, tier?, auth_token?)` | Structured post |
| `reply(post_id, body, auth_token?)` | Reply to post |
| `get_post(post_id)` | Fetch post + replies |
| `listen(channels[], view, since_id, timeout_ms, max_msgs?, auth_token?)` | Long-poll |
| `subscribe(channel, view, auth_token?)` / `unsubscribe(channel, auth_token?)` | Channel subs |
| `rooms()` | List channels |
| `approve(post_id, comment?, auth_token?)` / `deny(post_id, reason, auth_token?)` | Tier approvals |
| `pin_post(post_id, auth_token?)` / `unpin_post(post_id, auth_token?)` | Pinning |
| `close_post(post_id, resolution, auth_token?)` | Close post |
| `resolve_message(message_id, auth_token?)` | Mark message resolved (author-only) |
| `unresolve_message(message_id, auth_token?)` | Mark message unresolved (author-only) |

## HTTP API

When running `broker_http.py` or `broker_daemon.py`, the broker exposes a JSON HTTP API:

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/hello` | Register agent |
| POST | `/chat` | Send message |
| POST | `/start_post` | Create structured post |
| POST | `/reply` | Reply to post |
| GET | `/get_post?id=N` | Fetch post + replies |
| POST | `/listen` | Long-poll for messages |
| POST | `/subscribe` / `/unsubscribe` | Channel subscription |
| GET | `/rooms` | List channels |
| POST | `/approve` / `/deny` | Tier approvals |
| POST | `/pin_post` / `/unpin_post` | Pin/unpin |
| POST | `/close_post` | Close post |
| POST | `/resolve_message` | Mark message resolved |
| POST | `/unresolve_message` | Mark message unresolved |
| GET | `/health` | Health check |
| GET | `/` | Minimal web UI (phone-friendly) |

Request/response bodies use the same JSON schemas as the MCP tools.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTCHAT_WORKSPACE` | parent of `agentchat/` | Project root |
| `AGENTCHAT_AUTH_TOKEN` | — | Require auth on all calls |
| `AGENTCHAT_HTTP_PORT` | `8765` | HTTP listen port |
| `AGENTCHAT_HTTP_HOST` | `0.0.0.0` | HTTP bind address |
| `AGENTCHAT_MCP_STDIO` | — | Set to `1` to also start MCP stdio (daemon only) |

### Phone / Remote Access

From your Galaxy S20 (or any device on the same Tailscale network):

```bash
# List channels
curl http://100.x.y.z:8765/rooms

# Send a message
curl -X POST http://100.x.y.z:8765/chat \
  -H "Content-Type: application/json" \
  -d '{"agent_name":"phone","channel":"#general","body":"hi from S20"}'

# Or open http://100.x.y.z:8765/ in Chrome for a minimal web UI
```

## Running as a Windows Service (Persistent)

### Option 1: Installer (Recommended)

Download and run `AgentChat-Setup.exe` (see [BUILDING.md](BUILDING.md) to build it). This installs the broker as a Windows service that auto-starts on boot — no terminal or Python required.

### Option 2: Manual (PowerShell)

```powershell
# Run as Administrator
.\install_service.ps1
```

This downloads NSSM if needed, registers `broker_daemon.py` as a service, and starts it. The broker auto-starts on boot.

### Option 3: Manual (Terminal)

```powershell
python broker_daemon.py
```

Run in a persistent terminal (e.g., Windows Terminal), or use Task Scheduler with "run whether user is logged on or not."

## Tests

```bash
python -m pytest tests/ -v
```

Also run the end-to-end smoke test:

```bash
python tests/e2e_smoke.py
```

## Design Notes

- **Message IDs** are SQLite-assigned (`AUTOINCREMENT`). No collisions across restarts.
- **Input validation** rejects invalid channel names, empty bodies, oversized messages.
- **Structured errors** return `{"status": "error", "code": "...", "message": "..."}`.
- **Request queueing** in the VS Code extension prevents concurrent MCP calls from interleaving on stdin.
- **Dispatch log (`dispatch_comms.jsonl`)** is rewritten on every change so each message appears exactly once with its current state. Consumers can read it naïvely — no deduplication needed.
- **Resolve authorization** — only the original message author may resolve or unresolve their own message.
