"""`agent-peer send --await-reply`: send, then block for the reply in one call.
See docs/tasks/0008 (closes the send-then-wait race at its source)."""

import glob
import os
import unittest

from tests.helpers import isolated_home, run_cli, spawn_cli, stop_cli, wait_until

ALICE = "await-alice"
BOB = "await-bob"


def _sessions_dir(home):
    return os.path.join(home, ".claude", "sessions")


def _both_listening(home):
    return len(glob.glob(os.path.join(_sessions_dir(home), "*.json"))) >= 2


class AwaitReplyTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()
        self.alice = spawn_cli(["listen", "--name", ALICE], self.home)
        self.bob = spawn_cli(["listen", "--name", BOB], self.home)
        self.assertTrue(
            wait_until(lambda: _both_listening(self.home), timeout=5),
            "listeners never registered their sessions",
        )

    def tearDown(self):
        stop_cli(self.alice)
        stop_cli(self.bob)
        self._home_cm.__exit__(None, None, None)

    def test_reply_arrives_in_the_same_call(self):
        """send --await-reply returns exit 0 with the reply once the target
        answers - no separate wait call needed."""
        asker = spawn_cli(
            ["send", BOB, "ping?", "--sender", ALICE, "--await-reply", "5"],
            self.home,
        )
        try:
            reply = run_cli(["send", ALICE, "pong!", "--sender", BOB], self.home)
            self.assertEqual(reply.returncode, 0, reply.stderr)
            rc = asker.wait(timeout=10)
            out = asker.stdout.read()
        finally:
            stop_cli(asker)
        self.assertEqual(rc, 0, f"await-reply exited {rc}, output: {out}")
        self.assertIn("pong!", out)

    def test_timeout_when_nobody_replies(self):
        """No reply within the timeout: exit 1, and the original send still
        went out (delivery is not rolled back)."""
        result = run_cli(
            ["send", BOB, "hello?", "--sender", ALICE, "--await-reply", "1"],
            self.home,
        )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("Timeout", result.stdout)
        # the message itself was delivered despite the missed reply
        backlog = run_cli(["wait", "--name", BOB, "--timeout", "3"], self.home)
        self.assertEqual(backlog.returncode, 0, backlog.stderr)
        self.assertIn("hello?", backlog.stdout)

    def test_plain_send_does_not_block(self):
        """Without the flag, send returns right after delivery."""
        result = run_cli(["send", BOB, "fire and forget", "--sender", ALICE], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Delivered", result.stdout)
        self.assertNotIn("REPLY", result.stdout)


if __name__ == "__main__":
    unittest.main()
