#!/usr/bin/env python3
"""
AgentChat Audit Log — hash-chained append-only JSONL.

Each line is a JSON object with a SHA-256 hash chain linking it to the
previous entry.  A lightweight checkpoint file stores the latest sequence
number and hash for fast verification.

Design 2 (v1.5 security hardening):
  - Canonical JSON: sort_keys=True, separators=(",", ":"), ensure_ascii=False
  - Append-only writes (no rewrite, no deletion)
  - Atomic checkpoint writes (temp + os.replace)
  - Integrity violation on startup: archive broken log, start fresh, alert
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agentchat.audit")


def canonical_json(obj: dict) -> bytes:
    """Return canonical UTF-8 bytes for a JSON object."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash_entry(prev_hash: str, payload_bytes: bytes) -> str:
    """Compute SHA-256(prev_hash + payload_bytes)."""
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(payload_bytes)
    return h.hexdigest()


def _write_checkpoint(seq: int, hash_hex: str, path: Path) -> None:
    """Atomically write checkpoint file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(f"{seq}\n{hash_hex}\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


def _read_checkpoint(path: Path) -> tuple[int, str]:
    """Return (seq, hash_hex) from checkpoint, or (-1, '0'*64) if missing."""
    if not path.exists():
        return -1, "0" * 64
    text = path.read_text(encoding="utf-8").strip()
    lines = text.splitlines()
    if len(lines) < 2:
        return -1, "0" * 64
    return int(lines[0]), lines[1]


class AuditLog:
    """Append-only hash-chained audit log."""

    def __init__(
        self,
        log_path: Path,
        checkpoint_path: Path,
    ):
        self.log_path = log_path
        self.checkpoint_path = checkpoint_path
        self.archived_broken_path: Optional[Path] = None
        self._seq: int = -1
        self._prev_hash: str = "0" * 64
        self._lock = False  # simple re-entrancy guard (not thread-safe across processes)
        self._verify_on_startup()

    # ------------------------------------------------------------------
    # Startup integrity check
    # ------------------------------------------------------------------

    def _verify_on_startup(self) -> None:
        """Verify chain integrity on startup. If broken, archive and start fresh."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.log_path.exists() or self.log_path.stat().st_size == 0:
            # Fresh start
            self._seq = -1
            self._prev_hash = "0" * 64
            _write_checkpoint(self._seq, self._prev_hash, self.checkpoint_path)
            return

        # Read checkpoint
        cp_seq, cp_hash = _read_checkpoint(self.checkpoint_path)

        # Verify the chain
        last_seq = -1
        last_hash = "0" * 64
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    seq = entry.get("seq", line_no)
                    entry_hash = entry.get("hash", "")
                    # Recompute hash
                    payload = {k: v for k, v in entry.items() if k not in ("hash",)}
                    computed = _hash_entry(last_hash, canonical_json(payload))
                    if computed != entry_hash:
                        logger.error(
                            "Audit chain broken at seq %d (line %d): expected %s, got %s",
                            seq, line_no + 1, computed, entry_hash,
                        )
                        self._archive_broken()
                        return
                    last_seq = seq
                    last_hash = entry_hash
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            logger.error("Audit log unreadable (%s) — archiving and starting fresh", exc)
            self._archive_broken()
            return

        # Compare with checkpoint
        if cp_seq >= 0 and (last_seq != cp_seq or last_hash != cp_hash):
            logger.error(
                "Audit checkpoint mismatch: log ends at seq=%d hash=%s, checkpoint says seq=%d hash=%s",
                last_seq, last_hash, cp_seq, cp_hash,
            )
            self._archive_broken()
            return

        self._seq = last_seq
        self._prev_hash = last_hash
        logger.info("Audit log verified: %d entries, last_hash=%s...", self._seq + 1, last_hash[:16])

    def _archive_broken(self) -> None:
        """Rename broken log and checkpoint, start fresh."""
        ts = time.time()
        broken_log = self.log_path.with_suffix(f".broken.{ts:.6f}.jsonl")
        broken_cp = self.checkpoint_path.with_suffix(f".broken.{ts:.6f}.checkpoint")
        archived_path: Optional[Path] = None
        try:
            if self.log_path.exists():
                os.replace(str(self.log_path), str(broken_log))
                archived_path = broken_log
            if self.checkpoint_path.exists():
                os.replace(str(self.checkpoint_path), str(broken_cp))
        except OSError as exc:
            logger.error("Failed to archive broken audit log: %s", exc)
            # Fallback: truncate in place so we can still start fresh
            if self.log_path.exists():
                self.log_path.write_text("", encoding="utf-8")
                archived_path = self.log_path  # record that we cleared it in place
        self._seq = -1
        self._prev_hash = "0" * 64
        self.archived_broken_path = archived_path
        _write_checkpoint(self._seq, self._prev_hash, self.checkpoint_path)
        # Seed the fresh log with an INTEGRITY_VIOLATION entry
        if archived_path is not None:
            self.append(
                "INTEGRITY_VIOLATION",
                None,
                {"archived_to": str(archived_path.name), "reason": "chain verification failed on startup"},
            )
        logger.warning("Archived broken audit log to %s and started fresh", broken_log.name)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(self, event_type: str, agent: Optional[str], details: dict) -> None:
        """Append a new audit entry.  Thread-safe within one process."""
        if self._lock:
            return  # prevent re-entrancy
        self._lock = True
        try:
            self._seq += 1
            payload = {
                "seq": self._seq,
                "ts": time.time(),
                "event": event_type,
                "agent": agent,
            }
            payload.update(details)
            payload_bytes = canonical_json(payload)
            entry_hash = _hash_entry(self._prev_hash, payload_bytes)
            payload["hash"] = entry_hash

            line = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

            self._prev_hash = entry_hash
            _write_checkpoint(self._seq, self._prev_hash, self.checkpoint_path)
        finally:
            self._lock = False

    # ------------------------------------------------------------------
    # Verification (for external tools / tests)
    # ------------------------------------------------------------------

    def verify(self, full: bool = True) -> tuple[int, str]:
        """
        Verify the audit chain.

        Returns (exit_code, message).
        exit_code: 0=clean, 1=hash mismatch, 2=truncation (full mode only), 3=file error
        """
        if not self.log_path.exists():
            return 0, "No audit log to verify"

        last_seq = -1
        last_hash = "0" * 64
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    seq = entry.get("seq", line_no)
                    entry_hash = entry.get("hash", "")
                    payload = {k: v for k, v in entry.items() if k not in ("hash",)}
                    computed = _hash_entry(last_hash, canonical_json(payload))
                    if computed != entry_hash:
                        return 1, f"Hash mismatch at seq {seq} (line {line_no + 1})"
                    last_seq = seq
                    last_hash = entry_hash
        except (json.JSONDecodeError, OSError) as exc:
            return 3, f"File error at line {line_no + 1}: {exc}"

        if full:
            cp_seq, cp_hash = _read_checkpoint(self.checkpoint_path)
            if cp_seq >= 0 and (last_seq != cp_seq or last_hash != cp_hash):
                return 2, f"Truncation detected: log ends at seq={last_seq}, checkpoint says seq={cp_seq}"

        return 0, f"Chain verified: {last_seq + 1} entries"
