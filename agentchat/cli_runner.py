#!/usr/bin/env python3
"""Step-4 turn runner: spawn a REAL headless agent turn via the Claude/Kimi CLI.

Drop-in replacement for the stub `turn_runner` in supervisor.py. It:
  1. resolves the command + env from engines.resolve_spawn (subscription OR api,
     per the operator/other-user split — A.6.1),
  2. spawns `claude -p` / `kimi -p` with --output-format stream-json,
  3. hands the wake message to the agent as DATA,
  4. parses the stream-json transcript into the {did_work, session_id, output}
     contract the supervisor's guardrails expect,
  5. raises on a hard error so the supervisor's escalation path unwedges the
     agent (back to ASLEEP) instead of leaving it stuck.

One CLI invocation == one full agent turn (the agent loops over its own tool
calls internally and exits), so this returns did_work=False: the supervisor
does not self-continue; the next message re-wakes via the doorbell.

STATUS: `parse_stream_json_events` is unit-tested on 3.9 (test_cli_runner.py).
The subprocess orchestration needs a logged-in CLI to run end-to-end; the exact
stdin convention + event schema are marked VERIFY — confirm against the
installed `claude`/`kimi` before relying on the live path.
"""
import asyncio
import json
import os
from typing import Callable, Iterable, Optional

from engines import resolve_spawn, build_child_env


def parse_stream_json_events(lines: Iterable[str]) -> dict:
    """Reduce a CLI stream-json transcript to the turn-runner contract.

    Tolerant by design (schemas drift across CLI versions): scans every JSON
    line, takes session_id wherever it appears, collects assistant text, flags
    tool use, prefers the terminal `result` event's text, and surfaces errors.
    Returns {session_id, output, used_tools, error, complete}.
    """
    session_id: Optional[str] = None
    final_text: Optional[str] = None
    assistant_text_parts = []
    used_tools = False
    error: Optional[str] = None
    saw_result = False

    for line in lines:
        line = line.strip() if isinstance(line, str) else ""
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue  # skip non-JSON noise (banners, warnings)
        if not isinstance(ev, dict):
            continue

        if ev.get("session_id"):
            session_id = ev["session_id"]

        etype = ev.get("type")
        if etype == "assistant":
            for block in (ev.get("message", {}) or {}).get("content", []) or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    assistant_text_parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    used_tools = True
        elif etype == "user":
            used_tools = True  # tool results flowing back => the agent acted
        elif etype == "result":
            saw_result = True
            if ev.get("is_error"):
                error = ev.get("result") or ev.get("error") or "unknown CLI error"
            if isinstance(ev.get("result"), str):
                final_text = ev["result"]

    output = final_text if final_text is not None else "".join(assistant_text_parts)
    return {
        "session_id": session_id,
        "output": output,
        "used_tools": used_tools,
        "error": error,
        "complete": saw_result,
    }


def _format_user_message(text: str) -> str:
    """One stream-json user message for the CLI's stdin. VERIFY schema/flag."""
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}})


def default_role_prompt(broker, agent: str) -> str:
    return (
        f"You are agent '{agent}' in a shared ChatBox workspace. "
        "Use the chatbox MCP tools: `listen` on your channels to read what's new, "
        "`reply`/`chat` where a response is needed, record durable facts with "
        "whitebox `append_observation`, then `ack` what you processed and stop. "
        "Do not invent work; if there is nothing to do, stop."
    )


def make_cli_turn_runner(agent_engine: dict, *,
                         message_for: Callable = default_role_prompt,
                         mirror: Optional[Callable] = None,
                         environ: Optional[dict] = None) -> Callable:
    """Build a turn_runner that spawns a real CLI turn.

    agent_engine: {"engine": "claude"|"kimi", "auth_mode": "subscription"|"api",
                   "secret"?: <key>, "model"?: <override>}.
    message_for(broker, agent) -> str: the data handed to the turn.
    mirror(line, agent): optional sink to stream raw stream-json lines into the
        room/viewer as they would arrive.
    """
    base_env = environ if environ is not None else dict(os.environ)

    async def run(broker, agent) -> dict:
        prev = broker.get_liveness(agent)
        resolved = resolve_spawn(
            agent_engine["engine"], agent_engine["auth_mode"],
            secret=agent_engine.get("secret"), model=agent_engine.get("model"),
            resume=prev.get("session_id"),
        )
        env = build_child_env(base_env, resolved)
        prompt = message_for(broker, agent)

        # VERIFY: stdin stream-json vs prompt-as-arg, against the installed CLI.
        proc = await asyncio.create_subprocess_exec(
            *resolved["argv"],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate(
            input=_format_user_message(prompt).encode("utf-8")
        )
        lines = stdout.decode("utf-8", "replace").splitlines()
        if mirror:
            for ln in lines:
                mirror(ln, agent)

        parsed = parse_stream_json_events(lines)
        if parsed["error"] or (proc.returncode not in (0, None)):
            reason = parsed["error"] or (
                stderr.decode("utf-8", "replace")[:500] or f"exit {proc.returncode}")
            # Raise so the supervisor's escalation path unwedges the agent.
            raise RuntimeError(f"{agent} CLI turn failed: {reason}")

        # One CLI invocation == one full turn; do not self-continue.
        return {"did_work": False, "session_id": parsed["session_id"],
                "output": parsed["output"]}

    return run
