#!/usr/bin/env python3
"""Engine + auth resolution for spawned agent turns (Appendix A.6).

Supports BOTH auth modes, per agent / per user:
  - "subscription": drive the vendor's logged-in CLI (Claude Pro/Max, native
    Kimi plan) with NO API key. Usage draws on that plan's own limits. The
    operator runs here.
  - "api": pay-per-token key/token via env — for other users who bring keys.

This module ONLY resolves (engine, auth_mode) -> {argv, env, unset}. It does NOT
spawn — the turn_runner does — so it is pure and unit-testable with no CLI
present. The critical safety property: subscription mode UNSETS any stray API
key/token in the child env, so the operator's subscription is used and never
silently billed to a pay-per-token API.

VERIFY markers: exact CLI flags / env names are best-effort and must be checked
against the installed `claude` / `kimi` once available. The RESOLVER LOGIC is
what the tests pin down; the default values are easy to correct in one place.
"""
from typing import Optional

# Per (engine, auth_mode) config. Keeping modes explicit captures reality:
# Kimi-over-API rides the same `claude` CLI against Moonshot's Anthropic-
# compatible endpoint (plan Option B), while Kimi-over-subscription uses the
# native `kimi` CLI logged in with the plan (plan Option A).
DEFAULT_ENGINES = {
    "claude": {
        "subscription": {
            "cli": "claude",
            "base_args": ["-p", "--output-format", "stream-json", "--input-format", "stream-json"],
            "const_env": {},
            "token_var": None,                       # uses the CLI's stored OAuth login
            "unset": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"],
        },
        "api": {
            "cli": "claude",
            "base_args": ["-p", "--output-format", "stream-json", "--input-format", "stream-json"],
            "const_env": {},
            "token_var": "ANTHROPIC_API_KEY",
            "unset": [],
        },
    },
    "kimi": {
        # VERIFY: native Kimi CLI flags + whether it speaks MCP. Subscription
        # token comes from the CLI's own login, like Claude.
        "subscription": {
            "cli": "kimi",
            "base_args": ["-p", "--output-format", "stream-json"],
            "const_env": {},
            "token_var": None,
            "unset": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
        },
        # Option B: drive the Claude CLI against Moonshot's Anthropic-compatible API.
        "api": {
            "cli": "claude",
            "base_args": ["-p", "--output-format", "stream-json", "--input-format", "stream-json"],
            "const_env": {
                "ANTHROPIC_BASE_URL": "https://api.moonshot.ai/anthropic",
                "ANTHROPIC_MODEL": "kimi-k2.6",
            },
            "token_var": "ANTHROPIC_AUTH_TOKEN",
            "unset": ["ANTHROPIC_API_KEY"],
        },
    },
}

AUTH_MODES = ("subscription", "api")


def resolve_spawn(engine: str, auth_mode: str, *, secret: Optional[str] = None,
                  model: Optional[str] = None, resume: Optional[str] = None,
                  config: Optional[dict] = None) -> dict:
    """Resolve how to spawn one headless turn.

    Returns {"argv": [...], "env": {set...}, "unset": [vars to remove...]}.
    Raises ValueError for an unknown engine/mode, a mode the engine doesn't
    define, or api mode without a secret.
    """
    config = config or DEFAULT_ENGINES
    if engine not in config:
        raise ValueError(f"unknown engine {engine!r} (have {sorted(config)})")
    if auth_mode not in AUTH_MODES:
        raise ValueError(f"unknown auth_mode {auth_mode!r} (use one of {AUTH_MODES})")
    modes = config[engine]
    if auth_mode not in modes:
        raise ValueError(f"engine {engine!r} has no {auth_mode!r} config")
    spec = modes[auth_mode]

    argv = [spec["cli"], *spec["base_args"]]
    if resume:
        argv += ["--resume", resume]  # VERIFY flag name against the CLI

    env = dict(spec.get("const_env", {}))
    if model:
        env["ANTHROPIC_MODEL"] = model

    unset = list(spec.get("unset", []))
    token_var = spec.get("token_var")
    if auth_mode == "api":
        if not token_var:
            raise ValueError(f"engine {engine!r} api config defines no token_var")
        if not secret:
            raise ValueError(f"api auth for {engine!r} requires a secret (API key/token)")
        env[token_var] = secret
        unset = [v for v in unset if v != token_var]  # never unset the var we just set

    return {"argv": argv, "env": env, "unset": unset}


def build_child_env(base_environ: dict, resolved: dict) -> dict:
    """Apply a resolved spawn to a copy of base_environ: drop `unset` vars first
    (so a stray operator API key can't leak into a subscription turn), then set
    `env`. Returns the child process environment."""
    env = dict(base_environ)
    for var in resolved.get("unset", []):
        env.pop(var, None)
    env.update(resolved.get("env", {}))
    return env
