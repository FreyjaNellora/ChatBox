#!/usr/bin/env python3
"""
AgentChat Broker Core — shared business logic for all transports.

SQLite persistence, channel management, messaging, subscriptions,
approvals, and dispatch mirroring.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from audit_log import AuditLog

SESSION_TTL_SECONDS = 3600  # 1 hour default session lifetime

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("agentchat")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def _resolve_workspace_path() -> Path:
    env = os.environ.get("AGENTCHAT_WORKSPACE")
    if env:
        return Path(env).resolve()
    # PyInstaller sets sys._MEIPASS when running from a frozen executable.
    # In that case __file__ points inside the temp extraction dir, so we
    # fall back to the executable's directory (one level above the bundle).
    if getattr(sys, "_MEIPASS", None):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


WORKSPACE_ROOT = _resolve_workspace_path()
DB_PATH = WORKSPACE_ROOT / ".agentchat" / "db.sqlite"
DISPATCH_PATH = WORKSPACE_ROOT / "dispatch_comms.jsonl"
DISPATCH_STATE_PATH = WORKSPACE_ROOT / ".agentchat" / "dispatch_state.json"
AUDIT_PATH = WORKSPACE_ROOT / ".agentchat" / "audit.log.jsonl"
AUDIT_CHECKPOINT_PATH = WORKSPACE_ROOT / ".agentchat" / "audit.checkpoint"
STANDING_CHANNELS = [
    "#general",
    "#dispatch",
    "#change-orders",
    "#alerts",
    "#debug",
    "#observations",
]
MAX_MSGS_PER_LISTEN = 10
MAX_REQUEST_BYTES = 256 * 1024  # 256 KB request size cap


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BrokerError(Exception):
    """Base exception for broker operations."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class AuthError(BrokerError):
    def __init__(self, message: str = "Authentication failed", code: str = "AUTH_ERROR"):
        self.code = code
        super().__init__(code, message)


class ValidationError(BrokerError):
    def __init__(self, message: str):
        super().__init__("VALIDATION_ERROR", message)


class NotFoundError(BrokerError):
    def __init__(self, message: str):
        super().__init__("NOT_FOUND", message)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Agent:
    name: str
    phase: str
    subscriptions: dict[str, str] = field(default_factory=dict)
    last_seen: float = 0.0
    auth_token: Optional[str] = None
    session_token: Optional[str] = None
    session_expires: float = 0.0
    waiter: Optional[Any] = None  # asyncio.Future


@dataclass
class Channel:
    name: str
    description: str
    is_standing: bool
    created_at: float
    last_activity: float
    parent: Optional[str] = None


@dataclass
class Message:
    id: int
    channel: str
    author: str
    phase: str
    ts: float
    kind: str
    body: str
    in_reply_to: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    msg_type: Optional[str] = None
    tier: Optional[int] = None
    pinned: bool = False
    closed_resolution: Optional[str] = None
    resolved: bool = False


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_channel_name(name: str) -> None:
    if not isinstance(name, str) or not name.startswith("#") or len(name) < 2:
        raise ValidationError(f"Invalid channel name: {name!r}")


def _validate_agent_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip() or len(name) > 64:
        raise ValidationError(f"Invalid agent name: {name!r}")


def _validate_body(body: str) -> None:
    if not isinstance(body, str) or not body.strip():
        raise ValidationError("Message body cannot be empty")
    if len(body) > 100_000:
        raise ValidationError("Message body exceeds 100KB limit")


# ---------------------------------------------------------------------------
# Broker
# ---------------------------------------------------------------------------


class Broker:
    def __init__(
        self,
        db_path: Path = DB_PATH,
        dispatch_path: Path = DISPATCH_PATH,
        dispatch_state_path: Path = DISPATCH_STATE_PATH,
        workspace_root: Path = WORKSPACE_ROOT,
        auth_token: Optional[str] = None,
        audit_path: Path = AUDIT_PATH,
        audit_checkpoint_path: Path = AUDIT_CHECKPOINT_PATH,
    ):
        self.db_path = db_path
        self.dispatch_path = dispatch_path
        self.dispatch_state_path = dispatch_state_path
        self.workspace_root = workspace_root
        self.required_auth_token = auth_token
        self._previous_auth_token: Optional[str] = None  # for rotation grace period
        self.agents: dict[str, Agent] = {}
        self.channels: dict[str, Channel] = {}
        self._lock = None  # set by transport if needed
        self._audit = AuditLog(audit_path, audit_checkpoint_path)
        self._ensure_paths()
        self._init_db()
        self._clear_stale_sessions()
        self._load_channels()
        self._ensure_phase_channels()
        self._post_integrity_alert_if_needed()

    def set_lock(self, lock: Any) -> None:
        """Provide an asyncio.Lock from the transport layer."""
        self._lock = lock

    def _ensure_paths(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dispatch_path.parent.mkdir(parents=True, exist_ok=True)

    def _post_integrity_alert_if_needed(self):
        """If the audit log was archived due to integrity violation, post to #alerts."""
        archived = self._audit.archived_broken_path
        if archived is None:
            return
        if "#alerts" not in self.channels:
            return
        msg = Message(
            id=0,
            channel="#alerts",
            author="broker",
            phase="system",
            ts=time.time(),
            kind="chat",
            body="Audit chain integrity violation detected\n\n"
                 f"Broken audit log archived to {archived.name}. "
                 "New log started fresh. "
                 "Run `python -m agentchat.verify_audit --chain-only` on the archived file to inspect.",
            title="Audit chain integrity violation detected",
            description=f"Broken audit log archived to {archived.name}. New log started fresh. "
                        "Run `python -m agentchat.verify_audit --chain-only` on the archived file to inspect.",
            msg_type="alert",
            tier=2,
        )
        self._insert_message(msg)
        self._update_channel_activity("#alerts")

    # -- DB lifecycle -------------------------------------------------------

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                name TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                is_standing INTEGER NOT NULL,
                created_at REAL NOT NULL,
                last_activity REAL NOT NULL,
                parent TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                author TEXT NOT NULL,
                phase TEXT NOT NULL,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                body TEXT NOT NULL,
                in_reply_to INTEGER,
                title TEXT,
                description TEXT,
                msg_type TEXT,
                tier INTEGER,
                pinned INTEGER DEFAULT 0,
                closed_resolution TEXT,
                resolved INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                agent TEXT NOT NULL,
                channel TEXT NOT NULL,
                view TEXT NOT NULL DEFAULT 'full',
                PRIMARY KEY (agent, channel)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                name TEXT PRIMARY KEY,
                phase TEXT NOT NULL,
                session_token_hash TEXT NOT NULL,
                session_issued_ts REAL NOT NULL,
                session_expires_ts REAL NOT NULL,
                last_seen_ts REAL NOT NULL
            )
        """)
        # Migration: add resolved column to pre-existing databases
        cur = conn.execute("PRAGMA table_info(messages)")
        columns = {row[1] for row in cur.fetchall()}
        if "resolved" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN resolved INTEGER DEFAULT 0")
        conn.commit()
        conn.close()

    def _clear_stale_sessions(self):
        """Invalidate all in-memory sessions on broker restart (Q2)."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DELETE FROM agents")
        conn.commit()
        conn.close()

    def _load_channels(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM channels")
        for row in cur.fetchall():
            self.channels[row["name"]] = Channel(
                name=row["name"],
                description=row["description"],
                is_standing=bool(row["is_standing"]),
                created_at=row["created_at"],
                last_activity=row["last_activity"],
                parent=row["parent"],
            )
        conn.close()
        for name in STANDING_CHANNELS:
            if name not in self.channels:
                self._create_channel_in_db(name, f"Standing channel {name}", True)
                self.channels[name] = Channel(
                    name=name,
                    description=f"Standing channel {name}",
                    is_standing=True,
                    created_at=time.time(),
                    last_activity=time.time(),
                )

    def _ensure_phase_channels(self):
        phases_dir = self.workspace_root / "phases"
        if not phases_dir.exists():
            return
        for phase_file in phases_dir.glob("phase-*.md"):
            stem = phase_file.stem
            channel_name = f"#{stem}"
            if channel_name not in self.channels:
                description = self._extract_phase_description(phase_file)
                self._create_channel_in_db(channel_name, description, True)
                self.channels[channel_name] = Channel(
                    name=channel_name,
                    description=description,
                    is_standing=True,
                    created_at=time.time(),
                    last_activity=time.time(),
                )

    def _extract_phase_description(self, phase_file: Path) -> str:
        try:
            with open(phase_file, "r", encoding="utf-8") as f:
                for line in f:
                    line_stripped = line.strip()
                    if line_stripped.startswith("## AgentChat Description"):
                        for desc_line in f:
                            desc = desc_line.strip()
                            if desc:
                                return desc
                    elif line_stripped.startswith("## ") and "Current State" in line_stripped:
                        for desc_line in f:
                            desc = desc_line.strip()
                            if desc and not desc.startswith("##"):
                                return desc
            return f"Phase channel {phase_file.stem}"
        except Exception as exc:
            logger.warning("Failed to extract phase description from %s: %s", phase_file, exc)
            return f"Phase channel {phase_file.stem}"

    def _create_channel_in_db(self, name: str, description: str, is_standing: bool, parent: Optional[str] = None):
        conn = sqlite3.connect(str(self.db_path))
        now = time.time()
        conn.execute(
            "INSERT OR IGNORE INTO channels (name, description, is_standing, created_at, last_activity, parent) VALUES (?, ?, ?, ?, ?, ?)",
            (name, description, int(is_standing), now, now, parent),
        )
        conn.commit()
        conn.close()

    def _update_channel_activity(self, name: str):
        conn = sqlite3.connect(str(self.db_path))
        now = time.time()
        conn.execute("UPDATE channels SET last_activity = ? WHERE name = ?", (now, name))
        conn.commit()
        conn.close()
        if name in self.channels:
            self.channels[name].last_activity = now

    def _insert_message(self, msg: Message) -> Message:
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.execute(
            """
            INSERT INTO messages (channel, author, phase, ts, kind, body, in_reply_to,
                                  title, description, msg_type, tier, pinned, closed_resolution, resolved)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                msg.channel, msg.author, msg.phase, msg.ts, msg.kind, msg.body,
                msg.in_reply_to, msg.title, msg.description, msg.msg_type, msg.tier,
                int(msg.pinned), msg.closed_resolution, int(msg.resolved),
            ),
        )
        conn.commit()
        msg.id = cur.lastrowid
        conn.close()
        return msg

    def _load_dispatch_state(self) -> dict:
        if self.dispatch_state_path.exists():
            try:
                with open(self.dispatch_state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load dispatch state: %s", exc)
        return {}

    def _save_dispatch_state(self, state: dict) -> None:
        self.dispatch_state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.dispatch_state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def _mirror_to_dispatch(self, msg: Message):
        if msg.channel != "#dispatch":
            return
        self.dispatch_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "id": msg.id,
            "type": msg.msg_type or "chat",
            "source": msg.author,
            "tier": msg.tier,
            "phase": msg.phase,
            "message": msg.body,
            "timestamp": msg.ts,
            "resolved": msg.resolved,
        }
        state = self._load_dispatch_state()
        state[str(msg.id)] = entry
        self._save_dispatch_state(state)
        # Rewrite the JSONL file from state so consumers always see a single,
        # current snapshot per message — no deduplication required.
        with open(self.dispatch_path, "w", encoding="utf-8") as f:
            for key in sorted(state.keys(), key=int):
                f.write(json.dumps(state[key]) + "\n")

    # -- Auth ---------------------------------------------------------------

    def _check_auth(self, agent_name: str, token: Optional[str] = None) -> Agent:
        agent = self.agents.get(agent_name)
        if not agent:
            raise AuthError(f"Agent {agent_name} not registered")
        if self.required_auth_token:
            if token is None:
                raise AuthError("Missing auth token")
            if not hmac.compare_digest(token, self.required_auth_token):
                raise AuthError("Invalid auth token")
        return agent

    def _verify_session(self, agent_name: str, session_token: Optional[str] = None) -> Agent:
        """Verify that the session token matches the agent's registered session."""
        agent = self.agents.get(agent_name)
        if not agent:
            raise AuthError(f"Agent {agent_name} not registered")
        if session_token is None:
            raise AuthError("Missing session token")
        if agent.session_token is None:
            raise AuthError("Agent has no active session")
        if not hmac.compare_digest(session_token, agent.session_token):
            raise AuthError("Invalid session token", code="IDENTITY_MISMATCH")
        if time.time() > agent.session_expires:
            raise AuthError("Session expired", code="SESSION_EXPIRED")
        agent.last_seen = time.time()
        return agent

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _audit_log(self, event_type: str, agent: Optional[str] = None, details: Optional[dict] = None) -> None:
        """Append an entry to the hash-chained audit log."""
        try:
            self._audit.append(event_type, agent, details or {})
        except Exception as exc:
            logger.error("Audit log failure (%s): %s", event_type, exc)

    # -- Public API ---------------------------------------------------------

    async def hello(self, name: str, phase: str, default_channels: list[str],
                    auth_token: Optional[str] = None,
                    session_token: Optional[str] = None) -> dict:
        _validate_agent_name(name)
        if self.required_auth_token:
            if auth_token is None:
                self._audit_log("AUTH_FAILURE", name, {"reason": "missing_auth_token", "action": "hello"})
                raise AuthError("Missing auth token")
            if not hmac.compare_digest(auth_token, self.required_auth_token):
                # Allow previous token during rotation grace period
                if self._previous_auth_token and hmac.compare_digest(auth_token, self._previous_auth_token):
                    pass  # accepted with grace
                else:
                    self._audit_log("AUTH_FAILURE", name, {"reason": "invalid_auth_token", "action": "hello"})
                    raise AuthError("Invalid auth token")

        now = time.time()
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row

        # Check for existing live session
        cur = conn.execute("SELECT * FROM agents WHERE name = ?", (name,))
        row = cur.fetchone()

        is_reregister = False
        if row is not None:
            # Case 1: Same process retry — token matches, refresh expiry
            if session_token and hmac.compare_digest(
                self._hash_token(session_token), row["session_token_hash"]
            ):
                if now < row["session_expires_ts"]:
                    new_expires = now + SESSION_TTL_SECONDS
                    conn.execute(
                        "UPDATE agents SET session_expires_ts = ?, last_seen_ts = ? WHERE name = ?",
                        (new_expires, now, name),
                    )
                    conn.commit()
                    conn.close()
                    # Rehydrate in-memory agent
                    agent = self.agents.get(name)
                    if agent:
                        agent.session_expires = new_expires
                        agent.last_seen = now
                    self._audit_log("SESSION_REFRESHED", name, {"expires": new_expires})
                    return {
                        "status": "ok", "agent": name, "phase": phase,
                        "subscribed": list(agent.subscriptions.keys()) if agent else [],
                        "session_token": session_token,
                    }
                # Expired but valid hash — rotate (Case 3)
                is_reregister = True
                # Fall through to new token generation
            else:
                # Case 2: Different process, live name, no/expired token
                if now < row["session_expires_ts"]:
                    conn.close()
                    self._audit_log("IMPERSONATION_ATTEMPT", name, {"reason": "NAME_TAKEN", "action": "hello"})
                    raise BrokerError("NAME_TAKEN", f"Agent name {name!r} is already registered")

        # Generate new session token
        new_session_token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(new_session_token)
        expires = now + SESSION_TTL_SECONDS

        conn.execute(
            """INSERT OR REPLACE INTO agents
               (name, phase, session_token_hash, session_issued_ts, session_expires_ts, last_seen_ts)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, phase, token_hash, now, expires, now),
        )
        conn.commit()
        conn.close()

        subs = {}
        for ch in default_channels:
            _validate_channel_name(ch)
            if ch not in self.channels:
                if ch.startswith("#phase-"):
                    desc = f"Phase {ch.replace('#phase-', '')} workspace"
                    self._create_channel_in_db(ch, desc, True)
                    self.channels[ch] = Channel(
                        name=ch, description=desc, is_standing=True,
                        created_at=time.time(), last_activity=time.time(),
                    )
                else:
                    desc = f"Ad-hoc channel created by {name}"
                    self._create_channel_in_db(ch, desc, False)
                    self.channels[ch] = Channel(
                        name=ch, description=desc, is_standing=False,
                        created_at=time.time(), last_activity=time.time(),
                    )
            subs[ch] = "full"
            conn = sqlite3.connect(str(self.db_path))
            conn.execute(
                "INSERT OR REPLACE INTO subscriptions (agent, channel, view) VALUES (?, ?, ?)",
                (name, ch, "full"),
            )
            conn.commit()
            conn.close()

        self.agents[name] = Agent(
            name=name, phase=phase, subscriptions=subs,
            last_seen=now, auth_token=auth_token,
            session_token=new_session_token, session_expires=expires,
        )
        event_type = "AGENT_REREGISTERED" if is_reregister else "AGENT_REGISTERED"
        self._audit_log(event_type, name, {"phase": phase, "channels": list(subs.keys())})
        return {
            "status": "ok", "agent": name, "phase": phase,
            "subscribed": list(subs.keys()),
            "session_token": new_session_token,
        }

    async def chat(self, agent_name: str, channel: str, body: str,
                   auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        _validate_channel_name(channel)
        _validate_body(body)
        self._verify_session(agent_name, session_token)
        result = await self._post_message(agent_name, channel, "chat", body)
        self._audit_log("CHAT", agent_name, {"channel": channel, "message_id": result.get("message_id")})
        return result

    async def start_post(self, agent_name: str, channel: str, title: str, description: str,
                         msg_type: str, tier: Optional[int] = None,
                         auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        _validate_channel_name(channel)
        if not isinstance(title, str) or not title.strip():
            raise ValidationError("Post title cannot be empty")
        if not isinstance(description, str) or not description.strip():
            raise ValidationError("Post description cannot be empty")
        if tier is not None and not isinstance(tier, int):
            raise ValidationError("tier must be an integer")

        agent = self._verify_session(agent_name, session_token)
        phase = agent.phase if agent else "unknown"
        msg = Message(
            id=0,
            channel=channel,
            author=agent_name,
            phase=phase,
            ts=time.time(),
            kind="post",
            body=f"{title}\n\n{description}",
            title=title,
            description=description,
            msg_type=msg_type,
            tier=tier,
        )
        self._insert_message(msg)
        self._update_channel_activity(channel)
        self._mirror_to_dispatch(msg)
        self._notify_waiters(channel, msg)
        self._audit_log("POST", agent_name, {"channel": channel, "post_id": msg.id, "type": msg_type})
        return {"status": "ok", "post_id": msg.id}

    async def reply(self, agent_name: str, post_id: int, body: str,
                    auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        _validate_body(body)
        agent = self._verify_session(agent_name, session_token)
        phase = agent.phase if agent else "unknown"

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM messages WHERE id = ? AND kind = 'post'", (post_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            raise NotFoundError(f"Post {post_id} not found")
        msg = Message(
            id=0,
            channel=row["channel"],
            author=agent_name,
            phase=phase,
            ts=time.time(),
            kind="reply",
            body=body,
            in_reply_to=post_id,
        )
        self._insert_message(msg)
        self._update_channel_activity(row["channel"])
        self._notify_waiters(row["channel"], msg)
        self._audit_log("REPLY", agent_name, {"post_id": post_id, "reply_id": msg.id})
        return {"status": "ok", "reply_id": msg.id}

    async def get_post(self, post_id: int) -> dict:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM messages WHERE id = ?", (post_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise NotFoundError(f"Post {post_id} not found")
        post = dict(row)
        cur = conn.execute("SELECT * FROM messages WHERE in_reply_to = ? ORDER BY ts", (post_id,))
        replies = [dict(r) for r in cur.fetchall()]
        conn.close()
        return {"status": "ok", "post": post, "replies": replies}

    async def listen(self, agent_name: str, channels: list[str], view: str,
                     since_id: int, timeout_ms: int, max_msgs: int = MAX_MSGS_PER_LISTEN,
                     auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        import asyncio

        agent = self._verify_session(agent_name, session_token)

        # Validate channels
        for ch in channels:
            _validate_channel_name(ch)

        # Park the waiter FIRST to avoid race between check and park
        fut = asyncio.get_event_loop().create_future()
        agent.waiter = fut
        agent.last_seen = time.time()

        try:
            messages = self._fetch_messages(channels, view, since_id, max_msgs)
            if messages:
                return {"status": "ok", "messages": messages, "timed_out": False}

            try:
                await asyncio.wait_for(fut, timeout=timeout_ms / 1000.0)
            except asyncio.TimeoutError:
                pass
        finally:
            agent.waiter = None

        messages = self._fetch_messages(channels, view, since_id, max_msgs)
        return {"status": "ok", "messages": messages, "timed_out": len(messages) == 0}

    def _fetch_messages(self, channels: list[str], view: str, since_id: int, max_msgs: int) -> list[dict]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row

        if view == "headlines":
            all_msgs = []
            for ch in channels:
                cur = conn.execute(
                    "SELECT * FROM messages WHERE channel = ? AND id > ? ORDER BY id DESC LIMIT 5",
                    (ch, since_id),
                )
                all_msgs.extend([dict(r) for r in cur.fetchall()])
            conn.close()
            all_msgs.sort(key=lambda m: m["id"])
            return all_msgs[:max_msgs]

        elif view == "digest":
            cutoff = time.time() - 1800
            placeholders = ",".join("?" * len(channels))
            cur = conn.execute(
                f"SELECT * FROM messages WHERE channel IN ({placeholders}) AND id > ? AND ts > ? ORDER BY id LIMIT ?",
                (*channels, since_id, cutoff, max_msgs),
            )
            msgs = [dict(r) for r in cur.fetchall()]
            conn.close()
            return msgs

        else:  # full
            placeholders = ",".join("?" * len(channels))
            cur = conn.execute(
                f"SELECT * FROM messages WHERE channel IN ({placeholders}) AND id > ? ORDER BY id LIMIT ?",
                (*channels, since_id, max_msgs),
            )
            msgs = [dict(r) for r in cur.fetchall()]
            conn.close()
            return msgs

    def _notify_waiters(self, channel: str, msg: Message):
        for agent in list(self.agents.values()):
            if channel in agent.subscriptions and agent.waiter and not agent.waiter.done():
                try:
                    agent.waiter.set_result(None)
                except Exception:
                    pass

    async def subscribe(self, agent_name: str, channel: str, view: str = "full",
                        auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        _validate_channel_name(channel)
        agent = self._verify_session(agent_name, session_token)
        if channel not in self.channels:
            raise NotFoundError(f"Channel {channel} not found")
        agent.subscriptions[channel] = view
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT OR REPLACE INTO subscriptions (agent, channel, view) VALUES (?, ?, ?)",
            (agent_name, channel, view),
        )
        conn.commit()
        conn.close()
        self._audit_log("SUBSCRIBE", agent_name, {"channel": channel, "view": view})
        return {"status": "ok", "channel": channel, "view": view}

    async def unsubscribe(self, agent_name: str, channel: str,
                          auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        agent = self._verify_session(agent_name, session_token)
        agent.subscriptions.pop(channel, None)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DELETE FROM subscriptions WHERE agent = ? AND channel = ?", (agent_name, channel))
        conn.commit()
        conn.close()
        self._audit_log("UNSUBSCRIBE", agent_name, {"channel": channel})
        return {"status": "ok", "channel": channel}

    async def rooms(self) -> dict:
        result = []
        for ch in self.channels.values():
            result.append({
                "name": ch.name,
                "description": ch.description,
                "is_standing": ch.is_standing,
                "last_activity": ch.last_activity,
            })
        return {"status": "ok", "channels": result}

    async def approve(self, agent_name: str, post_id: int, comment: Optional[str] = None,
                      auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        return await self._approval_action(agent_name, post_id, "approval", comment, auth_token, session_token)

    async def deny(self, agent_name: str, post_id: int, reason: str,
                   auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        return await self._approval_action(agent_name, post_id, "denial", reason, auth_token, session_token)

    async def _approval_action(self, agent_name: str, post_id: int, action_type: str, note: Optional[str],
                               auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        self._verify_session(agent_name, session_token)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM messages WHERE id = ? AND kind = 'post'", (post_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            raise NotFoundError(f"Post {post_id} not found")
        self.dispatch_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "type": action_type,
            "source": agent_name,
            "message": note or f"{action_type}: post {post_id}",
            "timestamp": time.time(),
            "resolved": True,
        }
        with open(self.dispatch_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        self._audit_log(action_type.upper(), agent_name, {"post_id": post_id, "note": note})
        return {"status": "ok", "action": action_type, "post_id": post_id}

    async def pin_post(self, agent_name: str, post_id: int,
                       auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        return await self._set_pin(post_id, True, agent_name, auth_token, session_token)

    async def unpin_post(self, agent_name: str, post_id: int,
                         auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        return await self._set_pin(post_id, False, agent_name, auth_token, session_token)

    async def _set_pin(self, post_id: int, pinned: bool, agent_name: str,
                       auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        self._verify_session(agent_name, session_token)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT author FROM messages WHERE id = ?", (post_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise NotFoundError(f"Post {post_id} not found")
        if row["author"] != agent_name:
            conn.close()
            self._audit_log("NOT_AUTHOR", agent_name, {"action": "pin" if pinned else "unpin", "post_id": post_id})
            raise AuthError("Only the message author can pin/unpin this message")
        conn.execute("UPDATE messages SET pinned = ? WHERE id = ?", (int(pinned), post_id))
        conn.commit()
        conn.close()
        self._audit_log("PIN" if pinned else "UNPIN", agent_name, {"post_id": post_id})
        return {"status": "ok", "post_id": post_id, "pinned": pinned}

    async def close_post(self, agent_name: str, post_id: int, resolution: str,
                         auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        if not isinstance(resolution, str) or not resolution.strip():
            raise ValidationError("Resolution cannot be empty")
        self._verify_session(agent_name, session_token)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT author FROM messages WHERE id = ?", (post_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise NotFoundError(f"Post {post_id} not found")
        if row["author"] != agent_name:
            conn.close()
            self._audit_log("NOT_AUTHOR", agent_name, {"action": "close", "post_id": post_id})
            raise AuthError("Only the message author can close this post")
        conn.execute(
            "UPDATE messages SET closed_resolution = ? WHERE id = ?",
            (resolution, post_id),
        )
        conn.commit()
        conn.close()
        self._audit_log("CLOSE", agent_name, {"post_id": post_id, "resolution": resolution})
        return {"status": "ok", "post_id": post_id, "resolution": resolution}

    async def resolve_message(self, agent_name: str, message_id: int,
                              auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        self._verify_session(agent_name, session_token)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise NotFoundError(f"Message {message_id} not found")
        # Authorization: only the original author may resolve their own message.
        if row["author"] != agent_name:
            conn.close()
            self._audit_log("NOT_AUTHOR", agent_name, {"action": "resolve", "message_id": message_id})
            raise AuthError("Only the message author can resolve this message")
        conn.execute("UPDATE messages SET resolved = 1 WHERE id = ?", (message_id,))
        conn.commit()
        conn.close()
        # Re-mirror to dispatch if this is a dispatch message so the resolved state updates
        if row["channel"] == "#dispatch":
            msg = Message(
                id=row["id"],
                channel=row["channel"],
                author=row["author"],
                phase=row["phase"],
                ts=row["ts"],
                kind=row["kind"],
                body=row["body"],
                in_reply_to=row["in_reply_to"],
                title=row["title"],
                description=row["description"],
                msg_type=row["msg_type"],
                tier=row["tier"],
                pinned=bool(row["pinned"]),
                closed_resolution=row["closed_resolution"],
                resolved=True,
            )
            self._mirror_to_dispatch(msg)
        self._audit_log("RESOLVE", agent_name, {"message_id": message_id})
        return {"status": "ok", "message_id": message_id, "resolved": True}

    async def unresolve_message(self, agent_name: str, message_id: int,
                                auth_token: Optional[str] = None, session_token: Optional[str] = None) -> dict:
        self._verify_session(agent_name, session_token)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise NotFoundError(f"Message {message_id} not found")
        if row["author"] != agent_name:
            conn.close()
            self._audit_log("NOT_AUTHOR", agent_name, {"action": "unresolve", "message_id": message_id})
            raise AuthError("Only the message author can unresolve this message")
        conn.execute("UPDATE messages SET resolved = 0 WHERE id = ?", (message_id,))
        conn.commit()
        conn.close()
        if row["channel"] == "#dispatch":
            msg = Message(
                id=row["id"],
                channel=row["channel"],
                author=row["author"],
                phase=row["phase"],
                ts=row["ts"],
                kind=row["kind"],
                body=row["body"],
                in_reply_to=row["in_reply_to"],
                title=row["title"],
                description=row["description"],
                msg_type=row["msg_type"],
                tier=row["tier"],
                pinned=bool(row["pinned"]),
                closed_resolution=row["closed_resolution"],
                resolved=False,
            )
            self._mirror_to_dispatch(msg)
        self._audit_log("UNRESOLVE", agent_name, {"message_id": message_id})
        return {"status": "ok", "message_id": message_id, "resolved": False}

    async def rotate_auth_token(self, current_token: Optional[str] = None) -> dict:
        """Rotate the admission auth token. Returns the new token.
        
        If current_token is provided, it must match the existing token.
        If no auth_token was configured, this becomes the first one.
        """
        if self.required_auth_token and current_token is not None:
            if not hmac.compare_digest(current_token, self.required_auth_token):
                raise AuthError("Invalid current auth token")
        new_token = secrets.token_urlsafe(32)
        self._previous_auth_token = self.required_auth_token
        self.required_auth_token = new_token
        self._audit_log("ADMISSION_TOKEN_ROTATED", None, {"previous": bool(self._previous_auth_token)})
        return {"status": "ok", "auth_token": new_token}

    async def _post_message(self, agent_name: str, channel: str, kind: str, body: str) -> dict:
        agent = self.agents.get(agent_name)
        phase = agent.phase if agent else "unknown"
        msg = Message(
            id=0,
            channel=channel,
            author=agent_name,
            phase=phase,
            ts=time.time(),
            kind=kind,
            body=body,
        )
        self._insert_message(msg)
        self._update_channel_activity(channel)
        self._mirror_to_dispatch(msg)
        self._notify_waiters(channel, msg)
        return {"status": "ok", "message_id": msg.id}
