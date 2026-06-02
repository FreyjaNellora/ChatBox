"""Engine + auth resolution tests (Appendix A.6) — subscription AND api.

Pure logic, no CLI, no model: runs on Python 3.9.
    python tests/test_engines.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agentchat/
from engines import resolve_spawn, build_child_env


class ResolveTests(unittest.TestCase):
    def test_claude_subscription_uses_no_key_and_strips_stray_key(self):
        r = resolve_spawn("claude", "subscription")
        self.assertEqual(r["argv"][0], "claude")
        self.assertNotIn("ANTHROPIC_API_KEY", r["env"])
        self.assertIn("ANTHROPIC_API_KEY", r["unset"])  # operator key must be cleared
        # A stray operator key in the parent env must NOT reach the child.
        child = build_child_env({"ANTHROPIC_API_KEY": "sk-operator", "PATH": "/x"}, r)
        self.assertNotIn("ANTHROPIC_API_KEY", child)
        self.assertEqual(child["PATH"], "/x")

    def test_claude_api_sets_key(self):
        r = resolve_spawn("claude", "api", secret="sk-user-123")
        self.assertEqual(r["env"]["ANTHROPIC_API_KEY"], "sk-user-123")
        self.assertEqual(r["unset"], [])
        child = build_child_env({}, r)
        self.assertEqual(child["ANTHROPIC_API_KEY"], "sk-user-123")

    def test_api_without_secret_raises(self):
        with self.assertRaises(ValueError):
            resolve_spawn("claude", "api")

    def test_kimi_api_uses_moonshot_endpoint_and_token(self):
        r = resolve_spawn("kimi", "api", secret="ms-key")
        self.assertEqual(r["env"]["ANTHROPIC_BASE_URL"], "https://api.moonshot.ai/anthropic")
        self.assertEqual(r["env"]["ANTHROPIC_AUTH_TOKEN"], "ms-key")
        self.assertEqual(r["env"]["ANTHROPIC_MODEL"], "kimi-k2.6")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", r["unset"])  # don't unset what we set

    def test_kimi_subscription_uses_native_cli_no_token(self):
        r = resolve_spawn("kimi", "subscription")
        self.assertEqual(r["argv"][0], "kimi")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", r["env"])
        self.assertIn("ANTHROPIC_AUTH_TOKEN", r["unset"])

    def test_model_and_resume_overrides(self):
        r = resolve_spawn("claude", "api", secret="k", model="claude-opus-4-8", resume="sess-9")
        self.assertEqual(r["env"]["ANTHROPIC_MODEL"], "claude-opus-4-8")
        self.assertIn("--resume", r["argv"])
        self.assertIn("sess-9", r["argv"])

    def test_unknown_engine_and_mode(self):
        with self.assertRaises(ValueError):
            resolve_spawn("gpt", "api", secret="k")
        with self.assertRaises(ValueError):
            resolve_spawn("claude", "freebie")

    def test_build_child_env_order_set_wins_after_unset(self):
        # unset happens first, then env set — so an explicitly set var survives.
        r = {"env": {"ANTHROPIC_MODEL": "kimi-k2.6"}, "unset": ["ANTHROPIC_MODEL"]}
        child = build_child_env({"ANTHROPIC_MODEL": "old"}, r)
        self.assertEqual(child["ANTHROPIC_MODEL"], "kimi-k2.6")


if __name__ == "__main__":
    unittest.main(verbosity=2)
