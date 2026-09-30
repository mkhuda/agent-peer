"""An empty lock file (owner died between open() and write()) must not wedge
every later caller; a fresh empty one is still an owner mid-acquire."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_peer import compat


def _empty_lock(path, age_seconds):
    open(path, "w").close()
    old = os.path.getmtime(path) - age_seconds
    os.utime(path, (old, old))


class EmptyLockTest(unittest.TestCase):
    def test_old_empty_lock_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.lock")
            _empty_lock(path, age_seconds=60)
            handle = compat.acquire_lock(path)
            self.assertIsNotNone(handle)
            with open(path) as f:
                self.assertEqual(f.read(), str(os.getpid()))
            compat.release_lock(handle)

    def test_fresh_empty_lock_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.lock")
            _empty_lock(path, age_seconds=0)
            self.assertIsNone(compat.acquire_lock(path))
            self.assertTrue(os.path.exists(path))

    def test_live_holder_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.lock")
            first = compat.acquire_lock(path)
            self.assertIsNotNone(first)
            self.assertIsNone(compat.acquire_lock(path))
            compat.release_lock(first)


if __name__ == "__main__":
    unittest.main()
