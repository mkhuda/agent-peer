"""
Basic sanity: the CLI must not crash on a machine that has never used it.
"""

import unittest

from tests.helpers import isolated_home, run_cli


class EmptyHomeTest(unittest.TestCase):
    def test_list_on_a_fresh_home_does_not_crash(self):
        with isolated_home() as home:
            result = run_cli(["list"], home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("No active Claude Code sessions found", result.stdout)

    def test_inbox_on_a_fresh_home_does_not_crash(self):
        with isolated_home() as home:
            result = run_cli(["inbox"], home)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_version_flag(self):
        with isolated_home() as home:
            result = run_cli(["--version"], home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("agent-peer", result.stdout)

    def test_send_to_a_nonexistent_session_fails_cleanly(self):
        with isolated_home() as home:
            result = run_cli(["send", "nobody-here", "hi"], home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not found", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
