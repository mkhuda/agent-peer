"""A Codex listener registers the cwd Codex recorded for its thread, not the shared daemon's."""

import glob
import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import REPO_ROOT, isolated_home, spawn_cli, stop_cli, wait_until


class CodexCwdTest(unittest.TestCase):
    def setUp(self):
        ctx = isolated_home()
        self.home = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.procs = []
        self.addCleanup(lambda: [stop_cli(p) for p in self.procs])

    def _state_db(self, name, rows):
        os.makedirs(os.path.join(self.home, ".codex"), exist_ok=True)
        con = sqlite3.connect(os.path.join(self.home, ".codex", name))
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT NOT NULL)")
        con.executemany("INSERT INTO threads VALUES (?, ?)", rows)
        con.commit()
        con.close()

    def _registered_cwd(self, *args):
        proc = spawn_cli(["listen", "--name", "zz-cwd", *args], self.home)
        self.procs.append(proc)
        own = os.path.join(self.home, ".agent-peer", "sessions")

        def entry():
            for path in glob.glob(os.path.join(own, "*.json")):
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        found = wait_until(entry, timeout=10)
        self.assertTrue(found, "listener never registered")
        return found["cwd"]

    def test_the_thread_cwd_from_codexs_database_is_registered(self):
        self._state_db("state_5.sqlite", [("T1", "/work/agent-peer"), ("T2", "/work/other")])
        self.assertEqual(self._registered_cwd("--codex-thread", "T1"), "/work/agent-peer")

    def test_the_newest_state_database_is_used(self):
        self._state_db("state_4.sqlite", [("T1", "/old")])
        self._state_db("state_5.sqlite", [("T1", "/new")])
        self.assertEqual(self._registered_cwd("--codex-thread", "T1"), "/new")

    def test_an_explicit_cwd_wins(self):
        self._state_db("state_5.sqlite", [("T1", "/work/agent-peer")])
        self.assertEqual(self._registered_cwd("--codex-thread", "T1", "--cwd", "/explicit"), "/explicit")

    def _baseline(self):
        # Same listener without a Codex thread: the cwd it would register when the database says nothing.
        cwd = self._registered_cwd()
        for proc in self.procs:
            stop_cli(proc)
        self.procs.clear()
        for path in glob.glob(os.path.join(self.home, ".agent-peer", "sessions", "*")):
            os.unlink(path)
        return cwd

    def test_an_unknown_thread_falls_back_to_the_process_cwd(self):
        baseline = self._baseline()
        self._state_db("state_5.sqlite", [("T1", "/work/agent-peer")])
        self.assertEqual(self._registered_cwd("--codex-thread", "missing"), baseline)

    def test_a_missing_or_unreadable_database_falls_back_to_the_process_cwd(self):
        baseline = self._baseline()
        self.assertEqual(self._registered_cwd("--codex-thread", "T1"), baseline)
        os.makedirs(os.path.join(self.home, ".codex"), exist_ok=True)
        with open(os.path.join(self.home, ".codex", "state_5.sqlite"), "w") as f:
            f.write("not a database")
        for proc in self.procs:
            stop_cli(proc)
        self.procs.clear()
        for path in glob.glob(os.path.join(self.home, ".agent-peer", "sessions", "*")):
            os.unlink(path)
        self.assertEqual(self._registered_cwd("--codex-thread", "T1"), baseline)

    def test_state_files_are_ordered_by_number_and_older_ones_are_searched(self):
        self._state_db("state_9.sqlite", [("T1", "/nine"), ("T9", "/only-in-nine")])
        self._state_db("state_10.sqlite", [("T1", "/ten")])
        self.assertEqual(self._registered_cwd("--codex-thread", "T1"), "/ten")
        for proc in self.procs:
            stop_cli(proc)
        self.procs.clear()
        for path in glob.glob(os.path.join(self.home, ".agent-peer", "sessions", "*")):
            os.unlink(path)
        self.assertEqual(self._registered_cwd("--codex-thread", "T9"), "/only-in-nine")

    def test_a_database_in_wal_mode_with_a_live_writer_is_readable(self):
        os.makedirs(os.path.join(self.home, ".codex"), exist_ok=True)
        writer = sqlite3.connect(os.path.join(self.home, ".codex", "state_5.sqlite"))
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT NOT NULL)")
        writer.execute("INSERT INTO threads VALUES ('T1', '/wal/work')")
        writer.commit()
        self.assertEqual(self._registered_cwd("--codex-thread", "T1"), "/wal/work")


if __name__ == "__main__":
    unittest.main()
