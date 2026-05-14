#!/usr/bin/env python3
"""
AgentChat Broker — HTTP transport.

Exposes the same Broker business logic from broker_core.py over a lightweight
JSON HTTP API using only the Python standard library.

Environment variables:
    AGENTCHAT_WORKSPACE   — project root (default: parent of agentchat/)
    AGENTCHAT_AUTH_TOKEN  — optional auth token
    AGENTCHAT_HTTP_PORT   — port to listen on (default: 8765)
    AGENTCHAT_HTTP_HOST   — bind address (default: 0.0.0.0)
"""

import asyncio
import hmac
import json
import logging
import os
import secrets
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, List, Optional, Union
from urllib.parse import parse_qs, urlparse

from broker_core import (
    Broker,
    BrokerError,
    MAX_MSGS_PER_LISTEN,
    MAX_REQUEST_BYTES,
    WORKSPACE_ROOT,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("agentchat.http")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_HTTP_PORT = int(os.environ.get("AGENTCHAT_HTTP_PORT", "8765"))
_HTTP_HOST = os.environ.get("AGENTCHAT_HTTP_HOST", "0.0.0.0")

# ---------------------------------------------------------------------------
# Auth token: default-deny. Generate on first run if not provided.
# ---------------------------------------------------------------------------

def _resolve_auth_token() -> str:
    env_token = os.environ.get("AGENTCHAT_AUTH_TOKEN")
    if env_token:
        return env_token
    token_file = WORKSPACE_ROOT / ".agentchat" / "token"
    if token_file.exists():
        return token_file.read_text().strip()
    # Generate a secure token and persist it
    token = secrets.token_urlsafe(32)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token)
    os.chmod(str(token_file), stat.S_IRUSR | stat.S_IWUSR)  # 0600
    logger.warning("=" * 60)
    logger.warning("AGENTCHAT_AUTH_TOKEN was not set.")
    logger.warning("A new auth token has been generated and saved to:")
    logger.warning("  %s", token_file)
    logger.warning("TOKEN (copy this): %s", token)
    logger.warning("=" * 60)
    return token

_AUTH_TOKEN = _resolve_auth_token()

# ---------------------------------------------------------------------------
# CORS: default same-origin only. Allow-list via env var if needed.
# ---------------------------------------------------------------------------

def _parse_cors_origins() -> Union[List[str], None]:
    raw = os.environ.get("AGENTCHAT_CORS_ORIGINS", "").strip()
    if raw == "*":
        return ["*"]
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return None  # same-origin only

_CORS_ORIGINS = _parse_cors_origins()

# ---------------------------------------------------------------------------
# Minimal web UI for phone browser access
# ---------------------------------------------------------------------------

_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AgentChat</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#c9d1d9;padding:12px;max-width:600px;margin:0 auto}
h1{color:#58a6ff;font-size:1.4rem;margin-bottom:12px}
.panel{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;margin-bottom:12px}
label{display:block;color:#8b949e;font-size:.75rem;margin-bottom:4px;text-transform:uppercase}
input,textarea,select{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;padding:8px;font-size:.9rem;margin-bottom:8px}
input:focus,textarea:focus,select:focus{outline:none;border-color:#58a6ff}
button{background:#238636;color:#fff;border:none;border-radius:6px;padding:8px 16px;font-size:.9rem;cursor:pointer}
button:active{background:#2ea043}
button.secondary{background:#21262d;border:1px solid #30363d}
.row{display:flex;gap:8px}
.row>*{flex:1}
#output{white-space:pre-wrap;font-family:monospace;font-size:.8rem;background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:8px;max-height:300px;overflow:auto;margin-top:8px}
.msg{border-left:3px solid #30363d;padding-left:8px;margin-bottom:8px}
.msg .author{color:#58a6ff;font-weight:600;font-size:.8rem}
.msg .body{font-size:.9rem;margin-top:2px}
.msg .meta{color:#8b949e;font-size:.7rem}
.pinned{border-left-color:#f0883e}
</style>
</head>
<body>
<h1>AgentChat</h1>

<div class="panel">
  <label>Agent Name</label>
  <input id="agent" value="phone" placeholder="your agent name">
  <label>Auth Token (required)</label>
  <input id="token" type="password" placeholder="paste token here">
  <div class="row">
    <button onclick="register()">Register</button>
    <button class="secondary" onclick="loadRooms()">Refresh Rooms</button>
  </div>
</div>

<div class="panel">
  <label>Channel</label>
  <select id="channel"><option value="#general">#general</option></select>
  <label>Message</label>
  <textarea id="body" rows="3" placeholder="type a message..."></textarea>
  <div class="row">
    <button onclick="sendChat()">Send Chat</button>
    <button class="secondary" onclick="loadMessages()">Load Messages</button>
  </div>
</div>

<div class="panel">
  <label>Output</label>
  <div id="output">Register to begin...</div>
</div>

<script>
const API = '';
let sinceId = 0;

async function api(method, path, body) {
  const authTok = localStorage.getItem('agentchat_token') || document.getElementById('token').value;
  const sessionTok = localStorage.getItem('agentchat_session');
  const opts = {method, headers:{'Content-Type':'application/json'}};
  if(authTok) {
    opts.headers['Authorization'] = 'Bearer ' + authTok;
    localStorage.setItem('agentchat_token', authTok);
  }
  if(sessionTok) {
    opts.headers['X-Agent-Token'] = sessionTok;
  }
  if(body) opts.body = JSON.stringify(body);
  const r = await fetch(API + path, opts);
  const j = await r.json().catch(()=>({status:'error',message:'Bad JSON'}));
  if(j.status !== 'ok') log('ERROR: ' + (j.message || j.code));
  return j;
}

function log(s) { document.getElementById('output').textContent = typeof s === 'string' ? s : JSON.stringify(s, null, 2); }

function payload(extra) {
  const base = {agent_name: document.getElementById('agent').value};
  return Object.assign(base, extra);
}

async function register() {
  const name = document.getElementById('agent').value;
  const tok = document.getElementById('token').value;
  localStorage.setItem('agentchat_token', tok);
  const body = {name, phase: 'remote', default_channels: ['#general']};
  const r = await api('POST','/hello', body);
  if(r.status === 'ok') {
    if(r.session_token) localStorage.setItem('agentchat_session', r.session_token);
    log('Registered as ' + r.agent);
    loadRooms();
  }
}

async function loadRooms() {
  const r = await api('GET','/rooms');
  if(r.status !== 'ok') return;
  const sel = document.getElementById('channel');
  sel.innerHTML = '';
  r.channels.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c.name; opt.textContent = c.name + ' — ' + c.description;
    sel.appendChild(opt);
  });
  log('Rooms: ' + r.channels.map(c=>c.name).join(', '));
}

async function sendChat() {
  const ch = document.getElementById('channel').value;
  const body = document.getElementById('body').value;
  if(!body.trim()) return;
  const r = await api('POST','/chat', payload({channel: ch, body}));
  if(r.status === 'ok') { log('Sent msg ' + r.message_id); document.getElementById('body').value=''; loadMessages(); }
}

async function loadMessages() {
  const ch = document.getElementById('channel').value;
  const r = await api('POST','/listen', payload({channels:[ch], view:'full', since_id: sinceId, timeout_ms: 100}));
  if(r.status !== 'ok') return;
  const out = document.getElementById('output');
  out.innerHTML = '';
  r.messages.forEach(m => {
    sinceId = Math.max(sinceId, m.id);
    const div = document.createElement('div');
    div.className = 'msg' + (m.pinned ? ' pinned' : '');
    div.innerHTML = `<div class="author">${m.author} <span class="meta">#${m.id} ${new Date(m.ts*1000).toLocaleTimeString()}</span></div><div class="body">${escapeHtml(m.body)}</div>`;
    out.appendChild(div);
  });
  if(r.messages.length === 0) out.textContent = 'No new messages.';
}

function escapeHtml(t) {
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}

// Auto-poll every 5s
setInterval(() => { if(sinceId > 0) loadMessages(); }, 5000);
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Broker instance (shared, thread-safe via asyncio loop in thread)
# ---------------------------------------------------------------------------

broker = Broker(auth_token=_AUTH_TOKEN)
broker.set_lock(asyncio.Lock())

# We run the broker's asyncio event loop in a dedicated daemon thread so that
# synchronous HTTP handlers can dispatch async broker calls safely.
_broker_loop: Optional[asyncio.AbstractEventLoop] = None
_broker_thread: Optional[threading.Thread] = None


def _start_broker_loop() -> asyncio.AbstractEventLoop:
    """Start a background asyncio event loop for the Broker."""
    global _broker_loop, _broker_thread

    def run_loop():
        loop = asyncio.new_event_loop()
        global _broker_loop
        _broker_loop = loop
        asyncio.set_event_loop(loop)
        logger.info("Broker event loop started in background thread")
        loop.run_forever()

    _broker_thread = threading.Thread(target=run_loop, daemon=True, name="broker-loop")
    _broker_thread.start()

    # Wait until the loop is ready
    while _broker_loop is None:
        pass
    return _broker_loop


def _run_async(coro) -> Any:
    """Schedule a coroutine on the broker's event loop and block for result."""
    if _broker_loop is None:
        raise RuntimeError("Broker event loop not started")
    future = asyncio.run_coroutine_threadsafe(coro, _broker_loop)
    return future.result()


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class BrokerHTTPHandler(BaseHTTPRequestHandler):
    """JSON HTTP API for AgentChat broker."""

    # Suppress default request logging — we do our own
    def log_message(self, format, *args):
        pass

    def _cors_origin(self) -> Optional[str]:
        origin = self.headers.get("Origin")
        if _CORS_ORIGINS is None:
            # Same-origin only: no CORS headers for cross-origin requests
            return None
        if "*" in _CORS_ORIGINS:
            return "*"
        if origin and origin in _CORS_ORIGINS:
            return origin
        return None

    def _send_json(self, status_code: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        cors = self._cors_origin()
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Agent-Token")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length == 0:
            return {}
        if content_length > MAX_REQUEST_BYTES:
            raise BrokerError("PAYLOAD_TOO_LARGE", f"Request body {content_length} bytes exceeds {MAX_REQUEST_BYTES} byte limit")
        body = self.rfile.read(content_length).decode("utf-8")
        return json.loads(body)

    def _error(self, code: str, message: str, status: int = 400):
        self._send_json(status, {"status": "error", "code": code, "message": message})

    def do_OPTIONS(self):
        self.send_response(204)
        cors = self._cors_origin()
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Agent-Token")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/rooms":
                result = _run_async(broker.rooms())
                self._send_json(200, result)

            elif path == "/get_post":
                post_id_str = query.get("id", [None])[0]
                if post_id_str is None:
                    return self._error("VALIDATION_ERROR", "Missing query param: id")
                post_id = int(post_id_str)
                result = _run_async(broker.get_post(post_id))
                self._send_json(200, result)

            elif path == "/health":
                self._send_json(200, {"status": "ok", "transport": "http"})

            elif path == "/":
                # Minimal web UI for phone browser access
                html = _INDEX_HTML
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            else:
                self._send_json(404, {"status": "error", "code": "NOT_FOUND", "message": f"Unknown endpoint: {path}"})

        except BrokerError as exc:
            logger.warning("Broker error on %s: %s", path, exc.message)
            self._error(exc.code, exc.message)
        except Exception as exc:
            logger.exception("Unexpected error on %s", path)
            self._error("INTERNAL_ERROR", str(exc), 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            body = self._read_json()
        except BrokerError as exc:
            if exc.code == "PAYLOAD_TOO_LARGE":
                broker._audit_log("PAYLOAD_TOO_LARGE", None, {"path": path, "detail": exc.message})
            return self._error(exc.code, exc.message)
        except json.JSONDecodeError as exc:
            return self._error("VALIDATION_ERROR", f"Invalid JSON: {exc}")

        try:
            result = self._dispatch_post(path, body)
            self._send_json(200, result)

        except BrokerError as exc:
            logger.warning("Broker error on %s: %s", path, exc.message)
            self._error(exc.code, exc.message)
        except KeyError as exc:
            logger.warning("Missing argument on %s: %s", path, exc)
            self._error("VALIDATION_ERROR", f"Missing required argument: {exc}")
        except Exception as exc:
            logger.exception("Unexpected error on %s", path)
            self._error("INTERNAL_ERROR", str(exc), 500)

    def _extract_auth_token(self, body: dict) -> Optional[str]:
        """Read admission auth token from Authorization header or JSON body."""
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        return body.get("auth_token")

    def _extract_session_token(self, body: dict) -> Optional[str]:
        """Read session token from X-Agent-Token header or JSON body."""
        session_header = self.headers.get("X-Agent-Token", "")
        if session_header:
            return session_header.strip()
        return body.get("session_token")

    def _check_auth_token(self, token: Optional[str]) -> None:
        if token is None:
            raise BrokerError("AUTH_ERROR", "Missing auth token. Pass via Authorization: Bearer <token> header or auth_token field.")
        if not hmac.compare_digest(token, _AUTH_TOKEN):
            raise BrokerError("AUTH_ERROR", "Invalid auth token")

    def _dispatch_post(self, path: str, body: dict) -> dict:
        # hello() uses the admission auth_token; all other endpoints use session_token
        if path == "/hello":
            auth_token = self._extract_auth_token(body)
            self._check_auth_token(auth_token)
            return _run_async(broker.hello(
                body["name"], body["phase"], body["default_channels"],
                auth_token=auth_token))

        # All other endpoints require a session token
        session_token = self._extract_session_token(body)

        if path == "/chat":
            return _run_async(broker.chat(
                body["agent_name"], body["channel"], body["body"],
                session_token=session_token))

        elif path == "/start_post":
            return _run_async(broker.start_post(
                body["agent_name"], body["channel"], body["title"],
                body["description"], body["type"], body.get("tier"),
                session_token=session_token))

        elif path == "/reply":
            return _run_async(broker.reply(
                body["agent_name"], body["post_id"], body["body"],
                session_token=session_token))

        elif path == "/listen":
            return _run_async(broker.listen(
                body["agent_name"], body["channels"], body["view"],
                body["since_id"], body["timeout_ms"],
                body.get("max_msgs", MAX_MSGS_PER_LISTEN),
                session_token=session_token))

        elif path == "/subscribe":
            return _run_async(broker.subscribe(
                body["agent_name"], body["channel"],
                body.get("view", "full"),
                session_token=session_token))

        elif path == "/unsubscribe":
            return _run_async(broker.unsubscribe(
                body["agent_name"], body["channel"],
                session_token=session_token))

        elif path == "/approve":
            return _run_async(broker.approve(
                body["agent_name"], body["post_id"], body.get("comment"),
                session_token=session_token))

        elif path == "/deny":
            return _run_async(broker.deny(
                body["agent_name"], body["post_id"], body["reason"],
                session_token=session_token))

        elif path == "/pin_post":
            return _run_async(broker.pin_post(
                body["agent_name"], body["post_id"],
                session_token=session_token))

        elif path == "/unpin_post":
            return _run_async(broker.unpin_post(
                body["agent_name"], body["post_id"],
                session_token=session_token))

        elif path == "/close_post":
            return _run_async(broker.close_post(
                body["agent_name"], body["post_id"], body["resolution"],
                session_token=session_token))

        elif path == "/resolve_message":
            return _run_async(broker.resolve_message(
                body["agent_name"], body["message_id"],
                session_token=session_token))

        elif path == "/unresolve_message":
            return _run_async(broker.unresolve_message(
                body["agent_name"], body["message_id"],
                session_token=session_token))

        else:
            raise BrokerError("NOT_FOUND", f"Unknown endpoint: {path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    logger.info("AgentChat HTTP broker starting (workspace: %s)", WORKSPACE_ROOT)
    logger.info("Auth token is configured")

    _start_broker_loop()

    server = HTTPServer((_HTTP_HOST, _HTTP_PORT), BrokerHTTPHandler)
    logger.info("HTTP server listening on http://%s:%d", _HTTP_HOST, _HTTP_PORT)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        server.server_close()
        if _broker_loop is not None:
            _broker_loop.call_soon_threadsafe(_broker_loop.stop)


if __name__ == "__main__":
    main()
