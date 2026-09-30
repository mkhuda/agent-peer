"""A DM queued to Codex carries its send time and a staleness note, since a busy
Codex takes queued items late."""

import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_peer import sender


class CodexDmHeaderTest(unittest.TestCase):
    def _queued_text(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["message"] = cmd[cmd.index("--message") + 1]
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"HOME": tmp}), \
                mock.patch.object(sender.shutil, "which", return_value="/bin/codex"), \
                mock.patch.object(sender.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(sender, "append_inbox"):
            sender._send_via_codex_queue(
                {"name": "codex-x", "pid": 1}, "tid", "do X", "boss", "/w", "now")
        return seen["message"]

    def test_header_has_send_time(self):
        self.assertRegex(self._queued_text().splitlines()[0], r"^\[from boss · /w · sent \d\d:\d\d:\d\d\]$")

    def test_body_and_stale_note_follow(self):
        text = self._queued_text()
        self.assertIn("\ndo X\n", text)
        self.assertIn("confirm with the sender", text)

    def test_header_without_time_is_unchanged(self):
        self.assertEqual(sender._with_sender_header("hi", "a", "/w"), "[from a · /w]\nhi")

    def test_native_claude_header_has_send_time(self):
        self.assertEqual(sender._with_sender_header("hi", "a", "/w", "09:05:01"), "[from a · /w · sent 09:05:01]\nhi")


if __name__ == "__main__":
    unittest.main()
