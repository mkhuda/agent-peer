"""
Inbox/cursor/lock files and directories must be owner-only.

Regression test for the file-permission gap found across all three external
reviews (agy, pi, opencode) plus the earlier weaknesses report: these files
carry full inter-agent message content in plain text but were created with
the default umask instead of chmod 0600/0700 like the socket and key file.
"""

import glob
import os
import stat
import unittest

from tests.helpers import isolated_home, run_cli, spawn_cli, stop_cli, wait_until

SESSION = "perm-test"


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class FilePermissionTest(unittest.TestCase):
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
        run_cli(["send", SESSION, "hello"], self.home)
        wait_until(lambda: glob.glob(
            os.path.join(self.home, ".agent-peer", "inboxes", "*.jsonl")
        ), timeout=3)

    def tearDown(self):
        stop_cli(self.listener)
        self._home_cm.__exit__(None, None, None)

    def test_agent_peer_directories_are_owner_only(self):
        base = os.path.join(self.home, ".agent-peer")
        for sub in ("", "inboxes", "cursors", "locks"):
            path = os.path.join(base, sub) if sub else base
            self.assertEqual(_mode(path), 0o700, f"{path} should be 0700")

    def test_global_inbox_file_is_owner_only(self):
        path = os.path.join(self.home, ".agent-peer", "inbox.jsonl")
        self.assertTrue(os.path.exists(path))
        self.assertEqual(_mode(path), 0o600)

    def test_session_inbox_file_is_owner_only(self):
        matches = glob.glob(os.path.join(self.home, ".agent-peer", "inboxes", "*.jsonl"))
        self.assertTrue(matches)
        for path in matches:
            self.assertEqual(_mode(path), 0o600, f"{path} should be 0600")

    def test_cursor_file_is_owner_only(self):
        run_cli(["wait", "--name", SESSION, "--timeout", "3"], self.home)
        matches = glob.glob(os.path.join(self.home, ".agent-peer", "cursors", "*.json"))
        self.assertTrue(matches)
        for path in matches:
            self.assertEqual(_mode(path), 0o600, f"{path} should be 0600")

    def test_lock_file_is_owner_only(self):
        run_cli(["wait", "--name", SESSION, "--timeout", "3"], self.home)
        matches = glob.glob(os.path.join(self.home, ".agent-peer", "locks", "*.lock"))
        self.assertTrue(matches)
        for path in matches:
            self.assertEqual(_mode(path), 0o600, f"{path} should be 0600")


if __name__ == "__main__":
    unittest.main()
