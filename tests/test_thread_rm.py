"""`agent-peer thread rm <name>` deletes one thread, keeps a copy, and never touches a thread in use or a sibling."""

import glob
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import isolated_home, run_cli, spawn_cli, stop_cli, wait_until


class ThreadRmTest(unittest.TestCase):
    def setUp(self):
        self._cm = isolated_home()
        self.home = self._cm.__enter__()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            stop_cli(p)
        self._cm.__exit__(None, None, None)

    def _post(self, thread, text="hello", sender="alice"):
        self.assertEqual(run_cli(["send", "--thread", thread, "--sender", sender, text], self.home).returncode, 0)

    def _peek(self, thread, name="bob"):
        run_cli(["thread", thread, "--name", name, "--timeout", "1"], self.home)

    def _files(self, sub, pattern):
        return glob.glob(os.path.join(self.home, ".agent-peer", sub, pattern))

    def test_removes_the_thread_and_its_state_but_keeps_a_copy_and_other_threads(self):
        self._post("gone", "keep this text")
        self._post("stay")
        self._peek("gone")
        self._peek("stay")
        out = run_cli(["thread", "rm", "gone"], self.home)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self._files("threads", "gone.*"), [])
        self.assertEqual(self._files("cursors", "*thread.gone.*") + self._files("cursors", "push.gone.*"), [])
        self.assertEqual(self._files("locks", "thread.gone.*"), [])
        self.assertTrue(self._files("threads", "stay.jsonl"))
        self.assertTrue(self._files("cursors", "thread.stay.bob.json"))
        backups = self._files("purged", "thread-gone-*.jsonl")
        self.assertEqual(len(backups), 1)
        with open(backups[0], encoding="utf-8") as f:
            self.assertIn("keep this text", f.read())
        listed = json.loads(run_cli(["thread", "list", "--json"], self.home).stdout)
        self.assertEqual([t["thread"] for t in listed], ["stay"])

    def test_a_thread_whose_name_extends_it_keeps_its_cursors(self):
        self._post("a")
        self._post("a.b")
        self._peek("a")
        self._peek("a.b")
        self.assertEqual(run_cli(["thread", "rm", "a"], self.home).returncode, 0)
        self.assertFalse(self._files("cursors", "thread.a.bob.json"))
        self.assertTrue(self._files("cursors", "thread.a.b.bob.json"))
        self.assertTrue(self._files("threads", "a.b.jsonl"))

    def test_refuses_while_a_member_is_polling_unless_forced(self):
        self._post("busy")
        proc = spawn_cli(["thread", "busy", "--name", "watcher"], self.home)
        self.procs.append(proc)
        self.assertTrue(wait_until(
            lambda: json.loads(run_cli(["thread", "list", "--json"], self.home).stdout)[0]["active"] == ["watcher"],
            timeout=10), "poller never showed as active")
        out = run_cli(["thread", "rm", "busy"], self.home)
        self.assertEqual(out.returncode, 1)
        self.assertIn("watcher is still polling", out.stderr)
        self.assertTrue(self._files("threads", "busy.jsonl"))
        out = run_cli(["thread", "rm", "busy", "--force"], self.home)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(self._files("threads", "busy.jsonl"))

    def test_unknown_and_unsafe_names_and_missing_name_are_refused(self):
        out = run_cli(["thread", "rm", "nope"], self.home)
        self.assertEqual(out.returncode, 1)
        self.assertIn("No thread named 'nope'", out.stderr)
        out = run_cli(["thread", "rm", "../escape"], self.home)
        self.assertEqual(out.returncode, 1)
        self.assertIn("not a valid thread name", out.stderr)
        self.assertEqual(run_cli(["thread", "rm"], self.home).returncode, 2)

    def test_rm_does_not_create_a_thread_named_rm(self):
        run_cli(["thread", "rm", "nope"], self.home)
        self.assertFalse(self._files("threads", "rm.*"))


if __name__ == "__main__":
    unittest.main()
