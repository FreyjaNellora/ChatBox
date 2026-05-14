#!/usr/bin/env python3
"""
Standalone audit log verification tool.

Usage:
    python verify_audit.py [--full|--chain-only]

Exit codes:
    0  clean
    1  hash mismatch (prints seq)
    2  truncation detected (--full only)
    3  file error
"""

import argparse
import sys
from pathlib import Path

from audit_log import AuditLog


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify AgentChat audit log integrity")
    parser.add_argument(
        "--full", action="store_true", default=True,
        help="Verify chain + checkpoint (default)",
    )
    parser.add_argument(
        "--chain-only", action="store_true", dest="chain_only",
        help="Verify chain only, ignore checkpoint",
    )
    parser.add_argument(
        "--log", type=Path, default=None,
        help="Path to audit.log.jsonl (default: .agentchat/audit.log.jsonl)",
    )
    parser.add_argument(
        "--checkpoint", type=Path, default=None,
        help="Path to audit.checkpoint (default: .agentchat/audit.checkpoint)",
    )
    args = parser.parse_args()

    full = not args.chain_only

    if args.log is None:
        from broker_core import WORKSPACE_ROOT
        log_path = WORKSPACE_ROOT / ".agentchat" / "audit.log.jsonl"
    else:
        log_path = args.log

    if args.checkpoint is None:
        from broker_core import WORKSPACE_ROOT
        cp_path = WORKSPACE_ROOT / ".agentchat" / "audit.checkpoint"
    else:
        cp_path = args.checkpoint

    # Use verify without constructing AuditLog (which would auto-archive on startup)
    from audit_log import _read_checkpoint, canonical_json, _hash_entry
    import json

    if not log_path.exists() or log_path.stat().st_size == 0:
        print("No audit log to verify")
        return 0

    last_seq = -1
    last_hash = "0" * 64
    try:
        with open(log_path, "r", encoding="utf-8") as f:
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
                    print(f"Hash mismatch at seq {seq} (line {line_no + 1})")
                    return 1
                last_seq = seq
                last_hash = entry_hash
    except (json.JSONDecodeError, OSError) as exc:
        print(f"File error at line {line_no + 1}: {exc}")
        return 3

    if full:
        cp_seq, cp_hash = _read_checkpoint(cp_path)
        if cp_seq >= 0 and (last_seq != cp_seq or last_hash != cp_hash):
            print(f"Truncation detected: log ends at seq={last_seq}, checkpoint says seq={cp_seq}")
            return 2

    print(f"Chain verified: {last_seq + 1} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
