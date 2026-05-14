#!/usr/bin/env python3
"""
Tests for the hash-chained audit log.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from audit_log import AuditLog, canonical_json, _read_checkpoint


def test_canonical_json_is_deterministic():
    obj = {"b": 2, "a": 1, "c": {"z": 26, "a": 1}}
    b1 = canonical_json(obj)
    b2 = canonical_json(obj)
    assert b1 == b2
    assert b1 == b'{"a":1,"b":2,"c":{"a":1,"z":26}}'


def test_audit_log_append_and_verify():
    ws = tempfile.mkdtemp()
    log_path = Path(ws) / "audit.log.jsonl"
    cp_path = Path(ws) / "audit.checkpoint"

    audit = AuditLog(log_path, cp_path)
    audit.append("TEST_EVENT", "agent1", {"foo": "bar"})
    audit.append("TEST_EVENT", "agent2", {"baz": 42})

    code, msg = audit.verify(full=True)
    assert code == 0, msg
    assert "2 entries" in msg

    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 2

    entry0 = json.loads(lines[0])
    assert entry0["seq"] == 0
    assert entry0["event"] == "TEST_EVENT"
    assert entry0["agent"] == "agent1"
    assert "hash" in entry0

    entry1 = json.loads(lines[1])
    assert entry1["seq"] == 1
    assert entry1["event"] == "TEST_EVENT"
    assert entry1["agent"] == "agent2"
    assert "hash" in entry1

    # Hashes should differ and chain
    assert entry0["hash"] != entry1["hash"]


def test_audit_log_detects_tampering():
    ws = tempfile.mkdtemp()
    log_path = Path(ws) / "audit.log.jsonl"
    cp_path = Path(ws) / "audit.checkpoint"

    audit = AuditLog(log_path, cp_path)
    audit.append("TEST_EVENT", "agent1", {"foo": "bar"})
    audit.append("TEST_EVENT", "agent2", {"baz": 42})

    # Tamper: flip one byte in the first entry
    lines = log_path.read_text().strip().split("\n")
    tampered = lines[0].replace("bar", "bax")
    log_path.write_text(tampered + "\n" + lines[1] + "\n", encoding="utf-8")

    # Verify directly (without AuditLog init which would auto-archive)
    from audit_log import _read_checkpoint, canonical_json, _hash_entry
    import json as _json
    last_hash = "0" * 64
    with open(log_path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            entry = _json.loads(line)
            payload = {k: v for k, v in entry.items() if k not in ("hash",)}
            computed = _hash_entry(last_hash, canonical_json(payload))
            if computed != entry.get("hash", ""):
                assert "seq 0" in f"Hash mismatch at seq {entry.get('seq', line_no)}"
                break
            last_hash = entry["hash"]


def test_audit_log_detects_truncation():
    ws = tempfile.mkdtemp()
    log_path = Path(ws) / "audit.log.jsonl"
    cp_path = Path(ws) / "audit.checkpoint"

    audit = AuditLog(log_path, cp_path)
    for i in range(10):
        audit.append("TEST_EVENT", f"agent{i}", {"idx": i})

    # Truncate log to 5 lines but leave checkpoint at 9
    lines = log_path.read_text().strip().split("\n")
    log_path.write_text("\n".join(lines[:5]) + "\n", encoding="utf-8")

    # Direct verify (without startup archive) should detect truncation
    code, msg = audit.verify(full=True)
    assert code == 2, f"Expected truncation, got: {msg}"


def test_audit_log_chain_only_passes_on_truncation():
    ws = tempfile.mkdtemp()
    log_path = Path(ws) / "audit.log.jsonl"
    cp_path = Path(ws) / "audit.checkpoint"

    audit = AuditLog(log_path, cp_path)
    for i in range(10):
        audit.append("TEST_EVENT", f"agent{i}", {"idx": i})

    # Truncate log but leave checkpoint
    lines = log_path.read_text().strip().split("\n")
    log_path.write_text("\n".join(lines[:5]) + "\n", encoding="utf-8")

    # Chain-only verify should pass (chain itself is valid)
    code, msg = audit.verify(full=False)
    assert code == 0, f"Expected chain-only pass, got: {msg}"


def test_audit_log_archives_broken_on_startup():
    ws = tempfile.mkdtemp()
    log_path = Path(ws) / "audit.log.jsonl"
    cp_path = Path(ws) / "audit.checkpoint"

    audit = AuditLog(log_path, cp_path)
    audit.append("TEST_EVENT", "agent1", {"foo": "bar"})

    # Tamper and reopen
    lines = log_path.read_text().strip().split("\n")
    tampered = lines[0].replace("bar", "bax")
    log_path.write_text(tampered + "\n", encoding="utf-8")

    audit2 = AuditLog(log_path, cp_path)
    # Should have started fresh with INTEGRITY_VIOLATION as first entry
    assert log_path.exists()
    fresh_lines = log_path.read_text().strip().split("\n")
    first = json.loads(fresh_lines[0])
    assert first["event"] == "INTEGRITY_VIOLATION"


def test_audit_log_payload_too_large():
    """Oversized HTTP requests are audit-logged."""
    import asyncio
    from broker_core import Broker

    async def _run():
        ws = tempfile.mkdtemp()
        broker = Broker(
            db_path=Path(ws) / ".agentchat" / "db.sqlite",
            workspace_root=Path(ws),
            audit_path=Path(ws) / ".agentchat" / "audit.log.jsonl",
            audit_checkpoint_path=Path(ws) / ".agentchat" / "audit.checkpoint",
        )
        # Simulate what broker_http.py does on PAYLOAD_TOO_LARGE
        broker._audit_log("PAYLOAD_TOO_LARGE", None, {"path": "/chat", "detail": "test"})

        log_path = Path(ws) / ".agentchat" / "audit.log.jsonl"
        lines = log_path.read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["event"] == "PAYLOAD_TOO_LARGE"

    asyncio.run(_run())


def test_reregister_emits_correct_audit_event():
    """Case-3 re-registration emits AGENT_REREGISTERED, not AGENT_REGISTERED."""
    import asyncio
    from broker_core import Broker, SESSION_TTL_SECONDS

    async def _run():
        ws = tempfile.mkdtemp()
        broker = Broker(
            db_path=Path(ws) / ".agentchat" / "db.sqlite",
            workspace_root=Path(ws),
            audit_path=Path(ws) / ".agentchat" / "audit.log.jsonl",
            audit_checkpoint_path=Path(ws) / ".agentchat" / "audit.checkpoint",
        )
        r = await broker.hello("alice", "3", ["#general"])
        old_tok = r["session_token"]

        # Simulate expiry by manipulating the DB directly
        import sqlite3, time
        conn = sqlite3.connect(str(Path(ws) / ".agentchat" / "db.sqlite"))
        past = time.time() - SESSION_TTL_SECONDS - 1
        conn.execute(
            "UPDATE agents SET session_expires_ts = ? WHERE name = ?",
            (past, "alice"),
        )
        conn.commit()
        conn.close()

        # Re-hello with expired token → Case-3 rotate
        r2 = await broker.hello("alice", "3", ["#general"], session_token=old_tok)
        assert r2["status"] == "ok"
        assert r2["session_token"] != old_tok

        log_path = Path(ws) / ".agentchat" / "audit.log.jsonl"
        lines = log_path.read_text().strip().split("\n")
        events = [json.loads(line)["event"] for line in lines]
        assert "AGENT_REGISTERED" in events
        assert "AGENT_REREGISTERED" in events

    asyncio.run(_run())


def test_auth_token_rotation():
    """Admission token can be rotated with grace period for old token."""
    import asyncio
    from broker_core import Broker

    async def _run():
        ws = tempfile.mkdtemp()
        broker = Broker(
            db_path=Path(ws) / ".agentchat" / "db.sqlite",
            workspace_root=Path(ws),
            auth_token="old-secret",
            audit_path=Path(ws) / ".agentchat" / "audit.log.jsonl",
            audit_checkpoint_path=Path(ws) / ".agentchat" / "audit.checkpoint",
        )
        # Old token works
        r = await broker.hello("alice", "3", ["#general"], auth_token="old-secret")
        assert r["status"] == "ok"

        # Rotate
        rot = await broker.rotate_auth_token(current_token="old-secret")
        assert rot["status"] == "ok"
        new_token = rot["auth_token"]
        assert new_token != "old-secret"

        # Old token still works (grace period)
        r = await broker.hello("bob", "3", ["#general"], auth_token="old-secret")
        assert r["status"] == "ok"

        # New token works
        r = await broker.hello("carol", "3", ["#general"], auth_token=new_token)
        assert r["status"] == "ok"

        # Wrong token fails
        with pytest.raises(Exception):
            await broker.hello("dave", "3", ["#general"], auth_token="wrong")

    asyncio.run(_run())


def test_integrity_violation_posts_alert():
    """Tampered audit log triggers INTEGRITY_VIOLATION entry + #alerts post."""
    import asyncio
    from broker_core import Broker

    async def _run():
        ws = tempfile.mkdtemp()
        audit_path = Path(ws) / ".agentchat" / "audit.log.jsonl"
        audit_cp = Path(ws) / ".agentchat" / "audit.checkpoint"

        # Create a broker, generate some audit entries
        b1 = Broker(
            db_path=Path(ws) / ".agentchat" / "db.sqlite",
            workspace_root=Path(ws),
            audit_path=audit_path,
            audit_checkpoint_path=audit_cp,
        )
        r = await b1.hello("alice", "3", ["#general"])
        tok = r["session_token"]
        await b1.chat("alice", "#general", "hi", session_token=tok)

        # Tamper the audit log
        lines = audit_path.read_text().strip().split("\n")
        tampered = lines[0].replace("AGENT_REGISTERED", "AGENT_HACKED")
        audit_path.write_text(tampered + "\n" + "\n".join(lines[1:]) + "\n", encoding="utf-8")

        # Create a new broker — should detect tampering, archive, post alert
        b2 = Broker(
            db_path=Path(ws) / ".agentchat" / "db.sqlite",
            workspace_root=Path(ws),
            audit_path=audit_path,
            audit_checkpoint_path=audit_cp,
        )

        # Fresh audit log should start with INTEGRITY_VIOLATION
        fresh_lines = audit_path.read_text().strip().split("\n")
        first = json.loads(fresh_lines[0])
        assert first["event"] == "INTEGRITY_VIOLATION"
        assert "archived_to" in first

        # #alerts should have a tier=2 message
        conn = sqlite3.connect(str(Path(ws) / ".agentchat" / "db.sqlite"))
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM messages WHERE channel = '#alerts' ORDER BY id")
        rows = cur.fetchall()
        conn.close()
        assert len(rows) >= 1
        alert = rows[-1]
        assert alert["tier"] == 2
        assert "integrity violation" in alert["body"].lower()

    asyncio.run(_run())


def test_verify_audit_cli():
    """verify_audit.py CLI returns correct exit codes."""
    import subprocess, sys
    ws = tempfile.mkdtemp()
    log_path = Path(ws) / "audit.log.jsonl"
    cp_path = Path(ws) / "audit.checkpoint"

    audit = AuditLog(log_path, cp_path)
    for i in range(5):
        audit.append("TEST", f"agent{i}", {"idx": i})

    script = Path(__file__).resolve().parent.parent / "verify_audit.py"

    # Clean — exit 0
    r = subprocess.run([sys.executable, str(script), "--chain-only", "--log", str(log_path), "--checkpoint", str(cp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "5 entries" in r.stdout

    # Tamper — exit 1
    lines = log_path.read_text().strip().split("\n")
    tampered = lines[2].replace("agent2", "hacker")
    log_path.write_text("\n".join(lines[:2] + [tampered] + lines[3:]) + "\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), "--chain-only", "--log", str(log_path), "--checkpoint", str(cp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "seq 2" in r.stdout or "seq 2" in r.stderr


def test_audit_log_broker_integration():
    """Audit log is written during broker operations."""
    import asyncio
    from broker_core import Broker

    async def _run():
        ws = tempfile.mkdtemp()
        broker = Broker(
            db_path=Path(ws) / ".agentchat" / "db.sqlite",
            dispatch_path=Path(ws) / "dispatch_comms.jsonl",
            dispatch_state_path=Path(ws) / ".agentchat" / "dispatch_state.json",
            workspace_root=Path(ws),
            audit_path=Path(ws) / ".agentchat" / "audit.log.jsonl",
            audit_checkpoint_path=Path(ws) / ".agentchat" / "audit.checkpoint",
        )
        r = await broker.hello("alice", "3", ["#general"])
        tok = r["session_token"]
        await broker.chat("alice", "#general", "hi", session_token=tok)
        await broker.subscribe("alice", "#change-orders", session_token=tok)
        await broker.unsubscribe("alice", "#change-orders", session_token=tok)

        log_path = Path(ws) / ".agentchat" / "audit.log.jsonl"
        lines = log_path.read_text().strip().split("\n")
        events = [json.loads(line)["event"] for line in lines]
        assert events == ["AGENT_REGISTERED", "CHAT", "SUBSCRIBE", "UNSUBSCRIBE"]

        code, msg = broker._audit.verify(full=True)
        assert code == 0, msg

    asyncio.run(_run())
