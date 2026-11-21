"""`agent-peer thread list` shows every thread on the machine without waiting on or creating one."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import isolated_home, run_cli, spawn_cli, stop_cli, wait_until


class ThreadListTest(unittest.TestCase):
    def setUp(self):
        self._cm = isolated_home()
        self.home = self._cm.__enter__()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            stop_cli(p)
        self._cm.__exit__(None, None, None)

    def _post(self, thread, sender, text):
        self.assertEqual(run_cli(["send", "--thread", thread, "--sender", sender, text], self.home).returncode, 0)

    def _threads_dir(self):
        return os.path.join(self.home, ".agent-peer", "threads")

    def test_empty_machine_says_how_to_start_one(self):
        out = run_cli(["thread", "list"], self.home)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("No threads yet", out.stdout)

    def test_lists_every_thread_newest_first_with_last_sender(self):
        self._post("old-room", "alice", "first")
        self._post("new-room", "bob", "second")
        out = run_cli(["thread", "list", "--json"], self.home)
        self.assertEqual(out.returncode, 0, out.stderr)
        rows = json.loads(out.stdout)
        self.assertEqual([r["thread"] for r in rows], ["new-room", "old-room"])
        self.assertEqual(rows[0]["lastFrom"], "bob")
        self.assertEqual(rows[0]["seq"], 1)
        text = run_cli(["thread", "list"], self.home).stdout
        self.assertIn("new-room", text)
        self.assertIn("old-room", text)
        self.assertIn("Total: 2 threads", text)

    def test_a_running_poller_is_active_and_a_leaver_is_left(self):
        self._post("room", "alice", "hello")
        proc = spawn_cli(["thread", "room", "--name", "watcher"], self.home)
        self.procs.append(proc)
        self.assertTrue(wait_until(
            lambda: any(r["active"] == ["watcher"] for r in json.loads(run_cli(["thread", "list", "--json"], self.home).stdout)),
            timeout=10), "poller never showed as active")
        stop_cli(proc)
        self.assertEqual(run_cli(["thread", "room", "--name", "quitter", "--leave"], self.home).returncode, 0)
        self._post("room", "alice", "again")
        row = json.loads(run_cli(["thread", "list", "--json"], self.home).stdout)[0]
        self.assertNotIn("watcher", row["active"])

    def test_listing_does_not_create_a_thread_named_list(self):
        run_cli(["thread", "list"], self.home)
        self.assertFalse(os.path.exists(os.path.join(self._threads_dir(), "list.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self._threads_dir(), "list.presence.json")))

    def test_a_torn_last_line_does_not_break_the_listing(self):
        self._post("room", "alice", "ok")
        with open(os.path.join(self._threads_dir(), "room.jsonl"), "a", encoding="utf-8") as f:
            f.write('{"seq": 2, "ts"')
        out = run_cli(["thread", "list", "--json"], self.home)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)[0]["seq"], 1)


if __name__ == "__main__":
    unittest.main()
