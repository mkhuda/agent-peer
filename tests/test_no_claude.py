"""A machine without Claude Code: agent-peer must not create ~/.claude except when a listener
registers, and must not mistake that directory for an installation."""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_peer import harness_detect
from tests.helpers import REPO_ROOT, isolated_home, run_cli, spawn_cli, stop_cli, wait_until


class NoClaudeCreatedTest(unittest.TestCase):
    def test_commands_that_do_not_register_never_create_the_claude_directory(self):
        with isolated_home() as home:
            for args in (["send", "--thread", "t1", "hello"], ["thread", "t1", "--timeout", "1"],
                         ["list"], ["logs", "--thread", "t1"], ["inbox"]):
                run_cli(args, home)
            self.assertEqual(sorted(os.listdir(home)), [".agent-peer"])

    def test_listen_still_registers_where_it_always_did(self):
        with isolated_home() as home:
            proc = spawn_cli(["listen", "--name", "solo"], home)
            try:
                sessions = os.path.join(home, ".claude", "sessions")
                self.assertTrue(wait_until(lambda: os.path.isdir(sessions) and os.listdir(sessions), timeout=8))
            finally:
                stop_cli(proc)


class DetectClaudeTest(unittest.TestCase):
    def _detect(self, entries, on_path=None):
        with tempfile.TemporaryDirectory() as home:
            if entries is not None:
                os.makedirs(os.path.join(home, ".claude"))
                for name in entries:
                    os.makedirs(os.path.join(home, ".claude", name))
            with mock.patch.object(harness_detect.shutil, "which", return_value=on_path):
                return harness_detect.detect_claude(home)[0]

    def test_no_directory_and_no_binary_is_not_installed(self):
        self.assertFalse(self._detect(None))

    def test_a_directory_holding_only_our_sessions_is_not_an_installation(self):
        self.assertFalse(self._detect(["sessions"]))

    def test_a_populated_directory_is_an_installation(self):
        self.assertTrue(self._detect(["sessions", "projects"]))
        self.assertTrue(self._detect(["settings"]))

    def test_the_binary_on_path_is_an_installation_whatever_the_directory(self):
        self.assertTrue(self._detect(None, on_path="/usr/local/bin/claude"))

    def test_setup_list_after_a_listener_ran_does_not_report_claude(self):
        with isolated_home() as home:
            os.makedirs(os.path.join(home, ".claude", "sessions"))
            env = dict(os.environ, HOME=home, PATH="/usr/bin:/bin")
            out = subprocess.run([sys.executable, "-m", "agent_peer", "setup", "--list"], cwd=REPO_ROOT, env=env,
                                 capture_output=True, text=True).stdout
            line = next(ln for ln in out.splitlines() if ln.startswith("claude"))
            self.assertIn("not detected", line)


if __name__ == "__main__":
    unittest.main()
