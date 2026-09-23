"""Message content must survive round-trip untouched, whatever it contains -
backticks/quotes/newlines/paths are valid message text, not transport syntax.
Requested after a real incident report (codex-8763, 2026-09-23)."""

import glob
import json
import os
import unittest

from tests.helpers import isolated_home, run_cli, spawn_cli, stop_cli, wait_until

SESSION = "content-test"

TRICKY_PAYLOADS = [
    "inline `code` and `another` span",
    "a path: /usr/local/bin/`weird` and C:\\Users\\x\\`y`",
    "quotes: \"double\" and 'single' and `back`",
    "an endpoint: https://api.example.com/v1/`id`?q=`x`",
    "multi\nline\nmessage\nwith\n`backticks`\ntoo",
    "trailing backtick at the end`",
    "``double backticks`` and ```triple```",
]


class MessageContentTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()
        self.listener = spawn_cli(["listen", "--name", SESSION], self.home)
        self.assertTrue(
            wait_until(lambda: glob.glob(
                os.path.join(self.home, ".claude", "sessions", "*.json")
            ), timeout=5),
            "listener never registered a session",
        )

    def tearDown(self):
        stop_cli(self.listener)
        self._home_cm.__exit__(None, None, None)

    def test_tricky_payloads_survive_send_and_inbox_round_trip(self):
        for payload in TRICKY_PAYLOADS:
            with self.subTest(payload=payload):
                result = run_cli(["send", SESSION, payload], self.home)
                self.assertEqual(result.returncode, 0, result.stderr)

        inbox = run_cli(["inbox", "--name", SESSION, "--limit", str(len(TRICKY_PAYLOADS))], self.home)
        self.assertEqual(inbox.returncode, 0, inbox.stderr)
        for payload in TRICKY_PAYLOADS:
            self.assertIn(payload, inbox.stdout, f"payload not found intact: {payload!r}")

    def test_tricky_payload_survives_the_raw_json_frame(self):
        # Read the session inbox file directly - confirms the wire encoding
        # (format_user_frame's json.dumps) round-trips exactly, not just that
        # the CLI's own print happens to look right.
        payload = "`literal backticks` and a \"quote\" and a\nnewline"
        result = run_cli(["send", SESSION, payload], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

        inbox_files = glob.glob(os.path.join(self.home, ".agent-peer", "inboxes", "*.jsonl"))
        self.assertTrue(inbox_files)
        found = False
        for path in inbox_files:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    record = json.loads(line)
                    if record.get("content") == payload:
                        found = True
        self.assertTrue(found, "exact payload not found in any session inbox record")


if __name__ == "__main__":
    unittest.main()
