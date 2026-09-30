"""`agent-peer list` shows a Codex session as busy while a queued message has
been waiting (Codex only takes queued items when a turn ends)."""

import json
import os
import sqlite3
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import isolated_home, run_cli


class CodexBusyStatusTest(unittest.TestCase):
    def setUp(self):
        self._cm = isolated_home()
        self.home = self._cm.__enter__()
        self.proc = subprocess.Popen(["sleep", "60"])
        sessions = os.path.join(self.home, ".claude", "sessions")
        os.makedirs(sessions)
        self._session_path = os.path.join(sessions, f"{self.proc.pid}.json")
        self._write_session("idle")

    def tearDown(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)
        self._cm.__exit__(None, None, None)

    def _write_session(self, status):
        with open(self._session_path, "w", encoding="utf-8") as fh:
            json.dump({"name": "codex-x", "status": status, "agentType": "CODEX", "codexThreadId": "tid"}, fh)

    def _queue(self, ages_s):
        d = os.path.join(self.home, ".codex")
        os.makedirs(d, exist_ok=True)
        con = sqlite3.connect(os.path.join(d, "queue_1.sqlite"))
        con.execute(
            "CREATE TABLE IF NOT EXISTS queued_items (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, queue_order INTEGER NOT NULL, created_at_ms INTEGER NOT NULL, "
            "updated_at_ms INTEGER NOT NULL)"
        )
        con.execute("DELETE FROM queued_items")
        for i, age in enumerate(ages_s):
            con.execute(
                "INSERT INTO queued_items VALUES (?, 'tid', '{}', ?, ?, 0)",
                (f"i{i}", i, int((time.time() - age) * 1000)),
            )
        con.commit()
        con.close()

    def _status(self):
        out = run_cli(["list"], self.home).stdout
        line = next(ln for ln in out.splitlines() if "codex-x" in ln)
        return line.split()[3]

    def test_item_waiting_for_a_while_means_busy(self):
        self._queue([45])
        self.assertEqual(self._status(), "busy")

    def test_brand_new_item_is_not_yet_busy(self):
        self._queue([2])
        self.assertEqual(self._status(), "idle")

    def test_empty_queue_stays_idle(self):
        self._queue([])
        self.assertEqual(self._status(), "idle")

    def test_unreadable_queue_stays_as_registered(self):
        os.makedirs(os.path.join(self.home, ".codex"))
        with open(os.path.join(self.home, ".codex", "queue_1.sqlite"), "wb") as fh:
            fh.write(b"not a database")
        self.assertEqual(self._status(), "idle")

    def test_an_unread_message_marker_is_not_hidden_behind_busy(self):
        self._write_session("new-msg")
        self._queue([45])
        self.assertEqual(self._status(), "new-msg")

    def test_list_shows_unread_count_and_oldest_age(self):
        self._queue([600, 30, 5])
        out = run_cli(["list"], self.home).stdout
        line = next(ln for ln in out.splitlines() if "codex-x" in ln)
        self.assertIn("[3 unread, oldest 10m]", line)

    def test_empty_queue_shows_no_unread_marker(self):
        self._queue([])
        out = run_cli(["list"], self.home).stdout
        self.assertNotIn("unread", out)

    def test_unread_marker_survives_new_msg_status(self):
        self._write_session("new-msg")
        self._queue([45])
        out = run_cli(["list"], self.home).stdout
        self.assertIn("[1 unread, oldest 45s]", out)


if __name__ == "__main__":
    unittest.main()
