"""One machine, two agent-peer versions: the release before the own registry (v0.11.2, built from its
git tag) next to this tree. Skipped when the tag is not available."""

import glob
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import io
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import REPO_ROOT, isolated_env, isolated_home, run_cli, spawn_cli, stop_cli, wait_until

OLD_TAG = "v0.11.2"


def _extract_old_release(target):
    done = subprocess.run(["git", "archive", OLD_TAG, "agent_peer"], cwd=REPO_ROOT, capture_output=True)
    if done.returncode != 0:
        return False
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
        tar.extractall(target)
    path = os.path.join(target, "agent_peer", "protocol.py")
    with open(path, encoding="utf-8") as f:
        source = f.read()
    patched = source.replace('SOCKET_DIR = "/tmp/cc-socks"', 'SOCKET_DIR = os.environ.get("AGENT_PEER_SOCKET_DIR") or "/tmp/cc-socks"')
    if patched == source:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(patched)
    return True


class MixedVersionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_dir = tempfile.TemporaryDirectory(prefix="agent-peer-old-")
        if not _extract_old_release(cls._old_dir.name):
            cls._old_dir.cleanup()
            raise unittest.SkipTest(f"{OLD_TAG} is not available in this checkout")

    @classmethod
    def tearDownClass(cls):
        cls._old_dir.cleanup()

    def setUp(self):
        ctx = isolated_home()
        self.home = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.own = os.path.join(self.home, ".agent-peer", "sessions")
        self.claude = os.path.join(self.home, ".claude", "sessions")
        os.makedirs(self.claude)
        self.procs = []
        self.addCleanup(lambda: [stop_cli(p) for p in self.procs])

    def _old(self, args, timeout=15):
        return subprocess.run([sys.executable, "-m", "agent_peer", *args], cwd=self._old_dir.name,
                              env=isolated_env(self.home), capture_output=True, text=True, timeout=timeout)

    def _spawn_old(self, args):
        proc = subprocess.Popen([sys.executable, "-m", "agent_peer", *args], cwd=self._old_dir.name,
                                env=isolated_env(self.home), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(proc)
        return proc

    def _entry(self, directory, name):
        for path in glob.glob(os.path.join(directory, "*.json")):
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("name") == name:
                return meta

    def _row(self, out, name):
        rows = [l.split() for l in out.splitlines() if len(l.split()) > 4 and l.split()[1] == name]
        return rows[0] if rows else None

    def test_an_old_listener_works_with_this_tree(self):
        self._spawn_old(["listen", "--name", "zz-mix-old"])
        entry = wait_until(lambda: self._entry(self.claude, "zz-mix-old"), timeout=10)
        self.assertTrue(entry)
        row = self._row(run_cli(["list"], self.home).stdout, "zz-mix-old")
        self.assertEqual((row[0], row[4]), (str(entry["pid"]), "yes"))
        self.assertEqual(run_cli(["send", "zz-mix-old", "halo"], self.home).returncode, 0)
        self.assertTrue(wait_until(lambda: self._entry(self.claude, "zz-mix-old")["status"] == "new-msg", timeout=5))
        waited = run_cli(["wait", "--name", "zz-mix-old", "--timeout", "5"], self.home, timeout=15)
        self.assertIn("halo", waited.stdout)
        self.assertEqual(self._entry(self.claude, "zz-mix-old")["status"], "idle")
        self.assertFalse(os.path.exists(self.own))

    def test_this_trees_prune_clears_a_killed_old_listener(self):
        proc = self._spawn_old(["listen", "--name", "zz-mix-old2"])
        self.assertTrue(wait_until(lambda: self._entry(self.claude, "zz-mix-old2"), timeout=10))
        proc.kill()
        proc.wait(timeout=5)
        self.assertEqual(run_cli(["prune"], self.home).returncode, 0)
        self.assertEqual(os.listdir(self.claude), [])

    def test_the_old_release_reaches_a_listener_from_this_tree(self):
        proc = spawn_cli(["listen", "--name", "zz-mix-new"], self.home)
        self.procs.append(proc)
        entry = wait_until(lambda: self._entry(self.own, "zz-mix-new"), timeout=10)
        self.assertTrue(entry)
        row = self._row(self._old(["list"]).stdout, "zz-mix-new")
        self.assertEqual((row[0], row[4]), (str(entry["pid"]), "yes"))
        self.assertEqual(self._old(["send", "zz-mix-new", "halo"]).returncode, 0)
        self.assertTrue(wait_until(lambda: self._entry(self.own, "zz-mix-new")["status"] == "new-msg", timeout=5))
        self.assertIn("halo", self._old(["wait", "--name", "zz-mix-new", "--timeout", "5"]).stdout)
        # The old wait clears only the Claude-format copy; the merged view must still say idle.
        row = self._row(run_cli(["list"], self.home).stdout, "zz-mix-new")
        self.assertEqual(row[3], "idle")

    def test_an_old_prune_leaves_an_orphan_that_this_trees_prune_removes(self):
        proc = spawn_cli(["listen", "--name", "zz-mix-new2"], self.home)
        self.procs.append(proc)
        self.assertTrue(wait_until(lambda: self._entry(self.own, "zz-mix-new2"), timeout=10))
        proc.kill()
        proc.wait(timeout=5)
        self.assertEqual(self._old(["prune"]).returncode, 0)
        self.assertEqual(os.listdir(self.claude), [])
        self.assertTrue(os.listdir(self.own))
        self.assertEqual(run_cli(["prune"], self.home).returncode, 0)
        self.assertEqual(os.listdir(self.own), [])


if __name__ == "__main__":
    unittest.main()
