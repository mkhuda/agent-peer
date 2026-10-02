"""A listener removes only the name link that still points at its own socket."""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import REPO_ROOT, isolated_env, isolated_home

SCRIPT = """
import json, os, sys
from agent_peer.listener import PeerListener
listener = PeerListener(name="zz-clean")
target = listener.sock_path if sys.argv[1] == "own" else "/nonexistent-foreign"
os.symlink(target, listener.symlink_path)
listener.cleanup()
print(json.dumps({"link_left": os.path.islink(listener.symlink_path)}))
"""


class ListenerCleanupTest(unittest.TestCase):
    def _cleanup(self, whose):
        with isolated_home() as home:
            done = subprocess.run([sys.executable, "-c", SCRIPT, whose], cwd=REPO_ROOT, env=isolated_env(home),
                                  capture_output=True, text=True, timeout=15)
            self.assertEqual(done.returncode, 0, done.stderr)
            return json.loads(done.stdout)["link_left"]

    def test_its_own_link_is_removed(self):
        self.assertFalse(self._cleanup("own"))

    def test_a_link_to_another_live_peer_is_left_alone(self):
        self.assertTrue(self._cleanup("foreign"))


if __name__ == "__main__":
    unittest.main()
