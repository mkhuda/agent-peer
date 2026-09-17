"""The unread-cursor / backlog-merge contract for `agent-peer wait`.
See docs/wait-unread-cursor.md for the design."""

import glob
import json
import os
import unittest

from tests.helpers import isolated_home, run_cli, spawn_cli, stop_cli, wait_until

SESSION = "test-session"


def _sessions_dir(home):
    return os.path.join(home, ".claude", "sessions")


def _listener_ready(home):
    """True once the listener has registered itself (a session json exists)."""
    return bool(glob.glob(os.path.join(_sessions_dir(home), "*.json")))


class WaitBacklogTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()
        self.listener = spawn_cli(["listen", "--name", SESSION], self.home)
        self.assertTrue(
            wait_until(lambda: _listener_ready(self.home), timeout=5),
            "listener never registered a session",
        )

    def tearDown(self):
        stop_cli(self.listener)
        self._home_cm.__exit__(None, None, None)

    def send(self, message):
        result = run_cli(["send", SESSION, message], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_backlog_is_merged_into_one_wait_call(self):
        """Messages that arrive while nobody is waiting all come back in a
        single `wait` call, in order - not just the most recent one."""
        self.send("first")
        self.send("second")
        self.send("third")

        result = run_cli(["wait", "--name", SESSION, "--timeout", "3"], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("first", result.stdout)
        self.assertIn("second", result.stdout)
        self.assertIn("third", result.stdout)
        # order preserved
        self.assertLess(result.stdout.index("first"), result.stdout.index("second"))
        self.assertLess(result.stdout.index("second"), result.stdout.index("third"))

    def test_already_read_messages_are_not_replayed(self):
        """Once `wait` has returned a message, calling `wait` again must not
        hand it back a second time."""
        self.send("only once")
        first = run_cli(["wait", "--name", SESSION, "--timeout", "3"], self.home)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("only once", first.stdout)

        second = run_cli(["wait", "--name", SESSION, "--timeout", "1"], self.home)
        self.assertEqual(second.returncode, 1, "expected a timeout, not a replay")
        self.assertNotIn("only once", second.stdout)

    def test_new_message_after_cursor_advances_is_still_caught(self):
        self.send("first")
        run_cli(["wait", "--name", SESSION, "--timeout", "3"], self.home)

        self.send("second")
        result = run_cli(["wait", "--name", SESSION, "--timeout", "3"], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("second", result.stdout)
        self.assertNotIn("first", result.stdout)

    def test_clear_resets_the_cursor_without_reopening_the_old_gap(self):
        """`inbox --clear` must reset the cursor, not delete it - deleting
        it would let the next message be treated as "already old"."""
        self.send("before clear")
        run_cli(["wait", "--name", SESSION, "--timeout", "3"], self.home)

        clear = run_cli(["inbox", "--name", SESSION, "--clear"], self.home)
        self.assertEqual(clear.returncode, 0, clear.stderr)

        self.send("after clear")
        result = run_cli(["wait", "--name", SESSION, "--timeout", "3"], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("after clear", result.stdout)

    def test_concurrent_wait_for_the_same_session_is_rejected(self):
        """Only one `wait` may be in flight per session - a second one must
        fail fast instead of racing the first for the same cursor."""
        first = spawn_cli(["wait", "--name", SESSION, "--timeout", "5"], self.home)
        try:
            self.assertTrue(
                wait_until(lambda: os.path.exists(
                    os.path.join(self.home, ".agent-peer", "locks", f"{SESSION}.lock")
                ), timeout=3),
                "lock file was never created",
            )
            second = run_cli(["wait", "--name", SESSION, "--timeout", "3"], self.home)
            self.assertEqual(second.returncode, 1)
            self.assertIn("already running", second.stderr.lower())
        finally:
            stop_cli(first)

    def test_status_resets_to_idle_after_wait_reads_the_message(self):
        self.send("ping")

        session_file = glob.glob(os.path.join(_sessions_dir(self.home), "*.json"))[0]
        with open(session_file) as f:
            self.assertEqual(json.load(f)["status"], "new-msg")

        result = run_cli(["wait", "--name", SESSION, "--timeout", "3"], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)

        with open(session_file) as f:
            self.assertEqual(json.load(f)["status"], "idle")


if __name__ == "__main__":
    unittest.main()
