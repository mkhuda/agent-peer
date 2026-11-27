"""Behaviour that must hold on every platform but only ever broke on Windows: files another handle holds open,
a console code page that cannot print the status symbols, and paths with mixed separators."""

import unittest

from tests.helpers import isolated_home, run_py


class RetryBusyTest(unittest.TestCase):
    def test_a_file_held_for_an_instant_is_retried_then_the_error_surfaces(self):
        with isolated_home() as home:
            code = (
                "from agent_peer import compat, protocol\n"
                "compat.IS_WINDOWS = True\n"
                "calls = []\n"
                "def flaky():\n"
                "    calls.append(1)\n"
                "    if len(calls) < 3: raise PermissionError('held')\n"
                "    return 'ok'\n"
                "assert protocol.retry_busy(flaky, attempts=5, delay=0) == 'ok' and len(calls) == 3\n"
                "def stuck(): raise PermissionError('held')\n"
                "try:\n"
                "    protocol.retry_busy(stuck, attempts=3, delay=0)\n"
                "except PermissionError:\n"
                "    print('raised')\n"
            )
            out = run_py(home, code)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.strip(), "raised")

    def test_other_platforms_do_not_retry_a_real_permission_error(self):
        with isolated_home() as home:
            code = (
                "from agent_peer import compat, protocol\n"
                "compat.IS_WINDOWS = False\n"
                "n = []\n"
                "def denied():\n"
                "    n.append(1); raise PermissionError('no')\n"
                "try: protocol.retry_busy(denied)\n"
                "except PermissionError: print(len(n))\n"
            )
            self.assertEqual(run_py(home, code).stdout.strip(), "1")


class PathsTest(unittest.TestCase):
    def test_state_directories_are_built_without_mixed_separators(self):
        with isolated_home() as home:
            code = (
                "import os\n"
                "from agent_peer import protocol as p\n"
                "for v in (p.AGENT_PEER_DIR, p.SESSIONS_DIR, p.AGENT_SESSIONS_DIR):\n"
                "    assert v == os.path.normpath(v), v\n"
                "print('ok')\n"
            )
            out = run_py(home, code)
            self.assertEqual(out.stdout.strip(), "ok", out.stderr)


class OutboxSafetyTest(unittest.TestCase):
    def test_a_locked_registry_file_means_look_again_not_the_session_is_gone(self):
        with isolated_home() as home:
            code = (
                "import agent_peer.outbox as ob\n"
                "def locked(path): raise PermissionError('held')\n"
                "ob.read_text_retry = locked\n"
                "print(ob._state(1, None)[0])\n"
            )
            self.assertEqual(run_py(home, code).stdout.strip(), "busy")

    def test_the_drainer_does_not_pin_the_home_folder_as_its_working_directory(self):
        with isolated_home() as home:
            code = (
                "import os, subprocess\n"
                "import agent_peer.outbox as ob\n"
                "seen = {}\n"
                "class P:\n"
                "    def __init__(self, *a, **kw): seen.update(kw)\n"
                "subprocess.Popen = P\n"
                "ob._holder_alive = lambda pid: False\n"
                "ob.spawn_drainer(1)\n"
                "print(os.path.normcase(seen['cwd']) != os.path.normcase(os.path.expanduser('~')))\n"
            )
            self.assertEqual(run_py(home, code).stdout.strip(), "True")


if __name__ == "__main__":
    unittest.main()
