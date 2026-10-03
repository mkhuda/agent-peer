"""Where a listener registers: its own entry in ~/.agent-peer/sessions, plus a Claude-format copy
for senders that only read Claude's directory."""

import glob
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import isolated_home, run_cli, spawn_cli, stop_cli, wait_until

AUTO = {"AGENT_PEER_CLAUDE_MIRROR": "auto"}


class RegistryWriterTest(unittest.TestCase):
    def setUp(self):
        ctx = isolated_home()
        self.home = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.own = os.path.join(self.home, ".agent-peer", "sessions")
        self.claude = os.path.join(self.home, ".claude", "sessions")
        self.procs = []
        self.addCleanup(lambda: [stop_cli(p) for p in self.procs])

    def _listen(self, name, *args, env=None):
        proc = spawn_cli(["listen", "--name", name, *args], self.home, env_extra=env or AUTO)
        self.procs.append(proc)
        found = wait_until(lambda: self._entry(self.own, name), timeout=10)
        self.assertTrue(found, f"{name} never registered")
        return proc, found["pid"]

    @staticmethod
    def _entry(directory, name):
        for path in glob.glob(os.path.join(directory, "*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    meta = json.load(f)
            except ValueError:
                continue
            if meta.get("name") == name:
                return meta

    def _status(self, directory, name):
        return (self._entry(directory, name) or {}).get("status")

    def test_without_claude_only_the_own_entry_is_written(self):
        proc, pid = self._listen("zz-w-a")
        self.assertEqual(sorted(os.listdir(self.own)), [f"pid.{pid}.json", f"pid.{pid}.key"])
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude")))
        meta = self._entry(self.own, "zz-w-a")
        self.assertEqual((meta["transport"], meta["registryVersion"], meta["pid"]), ("socket", 1, pid))
        row = [l.split() for l in run_cli(["list"], self.home).stdout.splitlines() if "zz-w-a" in l][0]
        self.assertEqual((row[0], row[4]), (str(pid), "yes"))
        self.assertEqual(run_cli(["send", "zz-w-a", "halo"], self.home).returncode, 0)
        self.assertTrue(wait_until(lambda: self._status(self.own, "zz-w-a") == "new-msg", timeout=5))
        self.assertEqual(run_cli(["wait", "--name", "zz-w-a", "--timeout", "5"], self.home, timeout=15).returncode, 0)
        self.assertEqual(self._status(self.own, "zz-w-a"), "idle")
        stop_cli(proc)
        self.assertEqual(os.listdir(self.own), [])
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude")))

    def test_with_claude_a_claude_format_copy_follows_every_status_change(self):
        os.makedirs(self.claude)
        proc, pid = self._listen("zz-w-b")
        self.assertTrue(os.path.exists(os.path.join(self.claude, f"{pid}.json")))
        self.assertTrue(glob.glob(os.path.join(self.claude, f"{pid}.*.key")))
        self.assertTrue(re.match(rf"^{pid}\.[0-9a-f]{{64}}\.key$", os.path.basename(glob.glob(os.path.join(self.claude, "*.key"))[0])))
        self.assertEqual(run_cli(["send", "zz-w-b", "halo"], self.home).returncode, 0)
        for directory in (self.own, self.claude):
            self.assertTrue(wait_until(lambda: self._status(directory, "zz-w-b") == "new-msg", timeout=5), directory)
        run_cli(["wait", "--name", "zz-w-b", "--timeout", "5"], self.home, timeout=15)
        for directory in (self.own, self.claude):
            self.assertEqual(self._status(directory, "zz-w-b"), "idle", directory)
        listed = [l for l in run_cli(["list"], self.home).stdout.splitlines() if "zz-w-b" in l]
        self.assertEqual(len(listed), 1)
        stop_cli(proc)
        self.assertEqual(os.listdir(self.own), [])
        self.assertEqual(os.listdir(self.claude), [])

    def test_a_killed_listener_is_pruned_from_both_places(self):
        os.makedirs(self.claude)
        proc, pid = self._listen("zz-w-c")
        proc.kill()
        proc.wait(timeout=5)
        self.assertEqual(run_cli(["prune"], self.home).returncode, 0)
        self.assertEqual(os.listdir(self.own), [])
        self.assertEqual(os.listdir(self.claude), [])

    def test_the_mirror_switch(self):
        os.makedirs(self.claude)
        self._listen("zz-w-d", env={"AGENT_PEER_CLAUDE_MIRROR": "0"})
        self.assertEqual(os.listdir(self.claude), [])

    def test_the_mirror_can_be_forced_and_creates_the_directory(self):
        self._listen("zz-w-e", env={"AGENT_PEER_CLAUDE_MIRROR": "1"})
        self.assertTrue(glob.glob(os.path.join(self.claude, "*.json")))

    def test_legacy_registry_writes_only_claudes_directory(self):
        proc = spawn_cli(["listen", "--name", "zz-w-f"], self.home, env_extra={"AGENT_PEER_REGISTRY": "legacy"})
        self.procs.append(proc)
        self.assertTrue(wait_until(lambda: self._entry(self.claude, "zz-w-f"), timeout=10))
        self.assertFalse(os.path.exists(self.own))

    def test_a_codex_listener_is_keyed_by_its_thread(self):
        _, pid = self._listen("zz-w-g", "--codex-thread", "thread-1")
        self.assertEqual(sorted(os.listdir(self.own)), ["codex.thread-1.json", "codex.thread-1.key"])
        self.assertEqual(self._entry(self.own, "zz-w-g")["pid"], pid)

    def test_a_restarted_codex_listener_replaces_the_dead_ones_entry(self):
        first, pid1 = self._listen("zz-w-h", "--codex-thread", "thread-2")
        first.kill()
        first.wait(timeout=5)
        _, pid2 = self._listen("zz-w-h2", "--codex-thread", "thread-2")
        self.assertEqual(sorted(os.listdir(self.own)), ["codex.thread-2.json", "codex.thread-2.key"])
        self.assertEqual(self._entry(self.own, "zz-w-h2")["pid"], pid2)
        self.assertNotEqual(pid1, pid2)

    def test_a_listener_never_removes_an_entry_a_newer_listener_took_over(self):
        first, _ = self._listen("zz-w-i", "--codex-thread", "thread-3")
        proc = spawn_cli(["listen", "--name", "zz-w-i2", "--codex-thread", "thread-3", "--force"], self.home, env_extra=AUTO)
        self.procs.append(proc)
        self.assertTrue(wait_until(lambda: (self._entry(self.own, "zz-w-i2")), timeout=10))
        stop_cli(first)
        self.assertEqual((self._entry(self.own, "zz-w-i2") or {}).get("name"), "zz-w-i2")
        self.assertTrue(os.path.exists(os.path.join(self.own, "codex.thread-3.key")))


if __name__ == "__main__":
    unittest.main()
