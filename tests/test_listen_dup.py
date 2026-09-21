"""`agent-peer listen` refuses a second listener from the same harness session.
See docs/tasks/0015 (the codex-8763 vs codex-8763-2 incident)."""

import glob
import os
import unittest

from tests.helpers import isolated_home, run_cli, spawn_cli, stop_cli, wait_until

UID = "wX:listen-dup-probe"


def _session_count(home):
    return len(glob.glob(os.path.join(home, ".claude", "sessions", "*.json")))


class ListenDupTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()
        self._saved_env = {k: os.environ.get(k) for k in ("HERDR_ENV", "HERDR_PANE_ID")}
        os.environ["HERDR_ENV"] = "1"
        os.environ["HERDR_PANE_ID"] = UID
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            stop_cli(p)
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._home_cm.__exit__(None, None, None)

    def _first_listen(self, name="dup-a"):
        proc = spawn_cli(["listen", "--name", name], self.home)
        self.procs.append(proc)
        self.assertTrue(
            wait_until(lambda: _session_count(self.home) >= 1, timeout=5),
            "first listener never registered",
        )
        return proc

    def test_second_listen_refused_naming_old_session(self):
        """Same harness session id, different --name: refuse, name the old
        session, create nothing."""
        self._first_listen("dup-a")
        dup = run_cli(["listen", "--name", "dup-b"], self.home)
        self.assertNotEqual(dup.returncode, 0, dup.stdout)
        self.assertIn("dup-a", dup.stderr)
        self.assertIn("--force", dup.stderr)
        self.assertEqual(_session_count(self.home), 1)

    def test_force_keeps_both(self):
        """--force is the deliberate escape hatch for split-brain tests."""
        self._first_listen("dup-a")
        proc = spawn_cli(["listen", "--name", "dup-c", "--force"], self.home)
        self.procs.append(proc)
        self.assertTrue(
            wait_until(lambda: _session_count(self.home) >= 2, timeout=5),
            "forced second listener never registered",
        )

    def test_different_harness_session_allowed(self):
        """Two genuinely different sessions (different pane ids) still both
        register, even in one folder."""
        self._first_listen("dup-a")
        os.environ["HERDR_PANE_ID"] = UID + "-other"
        proc = spawn_cli(["listen", "--name", "dup-d"], self.home)
        self.procs.append(proc)
        self.assertTrue(
            wait_until(lambda: _session_count(self.home) >= 2, timeout=5),
            "different-session listener was wrongly refused",
        )


if __name__ == "__main__":
    unittest.main()
