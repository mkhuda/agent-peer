"""`agent-peer codex-queue` shows and empties one Codex thread's native queue, keeping a copy."""

import glob
import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import isolated_home, run_cli


class CodexQueuePurgeTest(unittest.TestCase):
    def setUp(self):
        ctx = isolated_home()
        self.home = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        os.makedirs(os.path.join(self.home, ".codex"))
        self.db = os.path.join(self.home, ".codex", "queue_1.sqlite")
        con = sqlite3.connect(self.db)
        con.executescript(
            "CREATE TABLE queued_items (id TEXT PRIMARY KEY, thread_id TEXT, payload_json TEXT,"
            " queue_order INTEGER, created_at_ms INTEGER, updated_at_ms INTEGER);"
            "CREATE TABLE queued_thread_revisions (revision INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT UNIQUE);"
            "INSERT INTO queued_thread_revisions(thread_id) VALUES ('T1');"
        )
        for i, (tid, text) in enumerate([("T1", "old one"), ("T1", "old two"), ("T2", "keep me")]):
            payload = json.dumps({"UserInput": {"content": [{"type": "text", "text": text}]}})
            con.execute("INSERT INTO queued_items VALUES (?,?,?,?,?,?)", (f"i{i}", tid, payload, i, 1_700_000_000_000, 0))
        con.commit()
        con.close()

    def _cli(self, *args):
        return run_cli(["codex-queue", *args], self.home, timeout=30)

    def _rows(self):
        con = sqlite3.connect(self.db)
        try:
            return con.execute("SELECT thread_id FROM queued_items ORDER BY queue_order").fetchall()
        finally:
            con.close()

    def test_show_counts_without_deleting(self):
        out = self._cli("--thread", "T1")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("2 message(s) waiting", out.stdout)
        self.assertEqual(len(self._rows()), 3)

    def test_purge_empties_only_that_thread_and_saves_a_copy(self):
        con = sqlite3.connect(self.db)
        before = con.execute("SELECT revision FROM queued_thread_revisions WHERE thread_id='T1'").fetchone()[0]
        con.close()
        out = self._cli("--thread", "T1", "--purge")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("removed 2 waiting message(s)", out.stdout)
        self.assertEqual(self._rows(), [("T2",)])
        con = sqlite3.connect(self.db)
        after = con.execute("SELECT revision FROM queued_thread_revisions WHERE thread_id='T1'").fetchone()[0]
        con.close()
        self.assertGreater(after, before)
        saved = glob.glob(os.path.join(self.home, ".agent-peer", "purged", "*.md"))
        self.assertEqual(len(saved), 1)
        with open(saved[0], encoding="utf-8") as f:
            text = f.read()
        self.assertIn("old one", text)
        self.assertIn("old two", text)
        self.assertNotIn("keep me", text)

    def test_purge_of_an_empty_queue_says_so(self):
        out = self._cli("--thread", "T9", "--purge")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("already empty", out.stdout)

    def test_an_unknown_session_is_refused(self):
        out = self._cli("nobody", "--purge")
        self.assertEqual(out.returncode, 1)
        self.assertIn("is not registered", out.stderr)
        self.assertEqual(len(self._rows()), 3)


if __name__ == "__main__":
    unittest.main()
