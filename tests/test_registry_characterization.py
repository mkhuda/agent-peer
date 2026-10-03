"""Session registry behaviour as users see it, through the real CLI in an isolated $HOME and socket
directory. Written before the registry refactor and kept unchanged across it."""

import glob
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import isolated_home, run_cli, socket_dir, spawn_cli, stop_cli, wait_until


class RegistryCharacterizationTest(unittest.TestCase):
    def setUp(self):
        ctx = isolated_home()
        self.home = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.sessions = os.path.join(self.home, ".claude", "sessions")
        self.procs = []
        self.addCleanup(lambda: [stop_cli(p) for p in self.procs])

    def _listen(self, name):
        proc = spawn_cli(["listen", "--name", name], self.home)
        self.procs.append(proc)

        def registered():
            for path in glob.glob(os.path.join(self.sessions, "*.json")):
                with open(path, encoding="utf-8") as f:
                    meta = json.load(f)
                if meta.get("name") == name:
                    return meta["pid"]
        pid = wait_until(registered, timeout=10)
        self.assertTrue(pid, f"{name} never registered")
        return proc, pid

    def _meta(self, pid):
        with open(os.path.join(self.sessions, f"{pid}.json"), encoding="utf-8") as f:
            return json.load(f)

    def _files(self, pid, name):
        keys = glob.glob(os.path.join(self.sessions, f"{pid}.*.key"))
        socks = socket_dir(self.home)
        return {
            "json": os.path.join(self.sessions, f"{pid}.json"),
            "key": keys[0] if keys else os.path.join(self.sessions, f"{pid}.MISSING.key"),
            "own_json": os.path.join(self.home, ".agent-peer", "sessions", f"pid.{pid}.json"),
            "own_key": os.path.join(self.home, ".agent-peer", "sessions", f"pid.{pid}.key"),
            "sock": os.path.join(socks, f"{pid}.sock"),
            "link": os.path.join(socks, f"{name}.sock"),
        }

    def _row(self, name):
        out = run_cli(["list"], self.home).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1] == name:
                return parts
        return None

    def test_listen_writes_json_key_and_sockets(self):
        _, pid = self._listen("zz-char-a")
        files = self._files(pid, "zz-char-a")
        for label, path in files.items():
            self.assertTrue(os.path.exists(path) or os.path.islink(path), f"{label} missing: {path}")
        meta = self._meta(pid)
        self.assertEqual((meta["name"], meta["pid"], meta["status"]), ("zz-char-a", pid, "idle"))
        self.assertTrue(meta["sessionId"])
        self.assertTrue(meta["messagingSocketPath"].endswith(f"{pid}.sock"))
        with open(files["key"], encoding="utf-8") as f:
            self.assertTrue(json.load(f)["peerToken"])
        self.assertTrue(re.match(rf"^{pid}\.[0-9a-f]{{64}}\.key$", os.path.basename(files["key"])))
        row = self._row("zz-char-a")
        self.assertEqual((row[0], row[3], row[4]), (str(pid), "idle", "yes"))

    def test_incoming_message_marks_new_msg_and_wait_resets_to_idle(self):
        _, pid = self._listen("zz-char-b")
        sent = run_cli(["send", "zz-char-b", "halo"], self.home)
        self.assertEqual(sent.returncode, 0, sent.stderr)
        self.assertTrue(wait_until(lambda: self._meta(pid)["status"] == "new-msg", timeout=5))
        self.assertIn("halo", self._meta(pid)["title"])
        waited = run_cli(["wait", "--name", "zz-char-b", "--timeout", "5"], self.home, timeout=15)
        self.assertEqual(waited.returncode, 0, waited.stderr)
        self.assertIn("halo", waited.stdout)
        self.assertEqual(self._meta(pid)["status"], "idle")

    def test_clean_exit_removes_every_file(self):
        proc, pid = self._listen("zz-char-c")
        files = self._files(pid, "zz-char-c")
        stop_cli(proc)
        for label, path in files.items():
            self.assertFalse(os.path.exists(path) or os.path.islink(path), f"{label} left behind: {path}")

    def test_killed_listener_lists_as_dead_and_prune_removes_its_files(self):
        proc, pid = self._listen("zz-char-d")
        files = self._files(pid, "zz-char-d")
        proc.kill()
        proc.wait(timeout=5)
        self.assertTrue(wait_until(lambda: (self._row("zz-char-d") or [None] * 5)[4] == "no", timeout=5))
        pruned = run_cli(["prune"], self.home)
        self.assertEqual(pruned.returncode, 0, pruned.stderr)
        self.assertIn("removed 6 file(s)", pruned.stdout)
        for label, path in files.items():
            self.assertFalse(os.path.exists(path) or os.path.islink(path), f"{label} survived prune: {path}")
        self.assertIsNone(self._row("zz-char-d"))


if __name__ == "__main__":
    unittest.main()
