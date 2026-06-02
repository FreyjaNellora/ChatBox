"""Stream-json parser tests for the step-4 CLI turn runner.

Pure parsing — no subprocess, no CLI: runs on Python 3.9.
    python tests/test_cli_runner.py
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agentchat/
from cli_runner import parse_stream_json_events, _format_user_message


def _t(obj):
    return json.dumps(obj)


class ParserTests(unittest.TestCase):
    def test_full_transcript_with_tools(self):
        lines = [
            _t({"type": "system", "subtype": "init", "session_id": "sess-1", "tools": ["listen"]}),
            _t({"type": "assistant", "session_id": "sess-1",
                "message": {"content": [{"type": "text", "text": "Reading the room."},
                                         {"type": "tool_use", "name": "mcp__chatbox__listen"}]}}),
            _t({"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}),
            _t({"type": "result", "subtype": "success", "is_error": False,
                "result": "Replied and acked.", "session_id": "sess-1"}),
        ]
        r = parse_stream_json_events(lines)
        self.assertEqual(r["session_id"], "sess-1")
        self.assertTrue(r["used_tools"])
        self.assertEqual(r["output"], "Replied and acked.")
        self.assertIsNone(r["error"])
        self.assertTrue(r["complete"])

    def test_session_id_from_system_when_result_lacks_it(self):
        lines = [
            _t({"type": "system", "session_id": "sess-9"}),
            _t({"type": "result", "result": "done"}),
        ]
        self.assertEqual(parse_stream_json_events(lines)["session_id"], "sess-9")

    def test_quiescent_no_tools_no_text(self):
        lines = [
            _t({"type": "system", "session_id": "s"}),
            _t({"type": "result", "is_error": False, "result": ""}),
        ]
        r = parse_stream_json_events(lines)
        self.assertFalse(r["used_tools"])
        self.assertEqual(r["output"], "")
        self.assertTrue(r["complete"])

    def test_error_result_is_surfaced(self):
        lines = [
            _t({"type": "system", "session_id": "s"}),
            _t({"type": "result", "is_error": True, "result": "rate limit hit"}),
        ]
        r = parse_stream_json_events(lines)
        self.assertEqual(r["error"], "rate limit hit")

    def test_text_accumulated_when_no_result_text(self):
        lines = [
            _t({"type": "assistant", "message": {"content": [{"type": "text", "text": "part one. "}]}}),
            _t({"type": "assistant", "message": {"content": [{"type": "text", "text": "part two."}]}}),
            _t({"type": "result", "is_error": False}),  # no result text field
        ]
        self.assertEqual(parse_stream_json_events(lines)["output"], "part one. part two.")

    def test_malformed_and_blank_lines_skipped(self):
        lines = ["", "not json at all", "{bad json", _t({"type": "result", "result": "ok"}), "   "]
        r = parse_stream_json_events(lines)
        self.assertEqual(r["output"], "ok")
        self.assertTrue(r["complete"])

    def test_format_user_message_roundtrips(self):
        msg = json.loads(_format_user_message("hello agent"))
        self.assertEqual(msg["type"], "user")
        self.assertEqual(msg["message"]["content"], "hello agent")


if __name__ == "__main__":
    unittest.main(verbosity=2)
