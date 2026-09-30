"""`thread --mention-only` stays silent on banter and wakes, with full context, on a mention."""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import isolated_home, run_cli, spawn_cli, stop_cli, wait_until


class MentionOnlyTest(unittest.TestCase):
    def setUp(self):
        self._cm = isolated_home()
        self.home = self._cm.__enter__()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            stop_cli(p)
        self._cm.__exit__(None, None, None)

    def _post(self, sender, text):
        self.assertEqual(run_cli(["send", "--thread", "t1", "--sender", sender, text], self.home).returncode, 0)

    def _arm(self, *flags):
        proc = spawn_cli(["thread", "t1", "--name", "agy-x", *flags], self.home)
        self.procs.append(proc)
        path = os.path.join(self.home, ".agent-peer", "threads", "t1.presence.json")
        self.assertTrue(wait_until(lambda: os.path.exists(path) and "agy-x" in open(path).read(), timeout=5))
        return proc

    def _cursor(self):
        path = os.path.join(self.home, ".agent-peer", "cursors", "thread.t1.agy-x.json")
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("last_seq", 0)

    def _still_waiting(self, proc, seconds=1.5):
        time.sleep(seconds)
        return proc.poll() is None

    def _woke_with(self, proc, *texts):
        self.assertTrue(wait_until(lambda: proc.poll() is not None, timeout=5), "did not wake")
        out = proc.stdout.read()
        self.assertEqual(proc.returncode, 0, out)
        for text in texts:
            self.assertIn(text, out)

    def test_banter_does_not_wake_and_leaves_the_cursor_alone(self):
        proc = self._arm("--mention-only")
        self._post("bob", "just chatting")
        self._post("bob", "still chatting")
        self.assertTrue(self._still_waiting(proc))
        self.assertEqual(self._cursor(), 0)

    def test_a_mention_wakes_with_the_banter_before_it_as_context(self):
        proc = self._arm("--mention-only")
        self._post("bob", "earlier banter")
        self._post("bob", "@agy-x over to you")
        self._woke_with(proc, "earlier banter", "@agy-x over to you")
        self.assertEqual(self._cursor(), 3)  # seq 1 is the room's own "joined" event

    def test_all_and_stop_wake_it(self):
        for text in ("@all heads up", "[stop] halt now"):
            proc = self._arm("--mention-only")
            self._post("bob", text)
            self._woke_with(proc, text)
            stop_cli(proc)
            self.procs.remove(proc)

    def test_a_mention_that_arrived_before_arming_is_returned_at_once(self):
        self._post("bob", "chat")
        self._post("bob", "@agy-x you there")
        out = run_cli(["thread", "t1", "--name", "agy-x", "--mention-only"], self.home, timeout=5)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("chat", out.stdout)
        self.assertIn("@agy-x you there", out.stdout)

    def test_a_mention_of_someone_else_or_inside_code_does_not_wake(self):
        proc = self._arm("--mention-only")
        self._post("bob", "@carol please look")
        self._post("bob", "the literal `@agy-x` token")
        self.assertTrue(self._still_waiting(proc))

    def test_own_post_does_not_wake(self):
        proc = self._arm("--mention-only")
        self._post("agy-x", "@agy-x note to self")
        self.assertTrue(self._still_waiting(proc))

    def test_presence_stays_active_while_waiting(self):
        self._arm("--mention-only")
        path = os.path.join(self.home, ".agent-peer", "threads", "t1.presence.json")
        with open(path, encoding="utf-8") as fh:
            self.assertFalse(json.load(fh)["agy-x"]["left"])

    def test_without_the_flag_any_post_still_wakes(self):
        first = self._arm()
        self._woke_with(first, "joined the thread")  # an unflagged arm returns on any unread post, even the join event
        proc = self._arm()
        self._post("bob", "plain banter")
        self._woke_with(proc, "plain banter")

    def test_combining_with_timeout_is_refused(self):
        out = run_cli(["thread", "t1", "--name", "agy-x", "--mention-only", "--timeout", "3"], self.home)
        self.assertEqual(out.returncode, 2)
        self.assertIn("--timeout", out.stderr)


if __name__ == "__main__":
    unittest.main()
