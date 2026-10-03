"""Guards around registry entries: collision-free file names, ownership by sessionId, one writer at a
time, rollback of a half-finished registration, and a prune that spares a replacement."""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import REPO_ROOT, isolated_env, isolated_home, run_cli, socket_dir


class RegistryGuardsTest(unittest.TestCase):
    def setUp(self):
        ctx = isolated_home()
        self.home = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.own = os.path.join(self.home, ".agent-peer", "sessions")

    def _py(self, code):
        done = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=isolated_env(self.home),
                              capture_output=True, text=True, timeout=30)
        return done

    def test_ids_that_look_alike_get_different_files_and_stay_inside_the_directory(self):
        done = self._py(
            "import json, os\n"
            "from agent_peer import registry\n"
            "ids = ['thread/a', 'thread_a', 'thread.a', '../../x', 'ünï']\n"
            "stems = [os.path.basename(registry.session_paths(1, 'k.key', ('codex', i))[0][0]) for i in ids]\n"
            "dirs = {os.path.dirname(registry.session_paths(1, 'k.key', ('codex', i))[0][0]) for i in ids}\n"
            "print(json.dumps({'stems': stems, 'dirs': len(dirs)}))\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(len(set(out["stems"])), 5)
        self.assertEqual(out["dirs"], 1)
        self.assertTrue(all("/" not in s and ".." not in s for s in out["stems"]))

    def test_an_older_listener_cannot_touch_the_entry_a_newer_one_took_over(self):
        done = self._py(
            "import json, os\n"
            "from agent_peer import registry\n"
            "copies = registry.session_paths(1, 'k.key', ('codex', 'T'))\n"
            "registry.ensure_sessions_dir(copies)\n"
            "registry.register_session(copies, {'peerToken': 'a'}, {'name': 'old', 'pid': 1, 'sessionId': 'old'})\n"
            "registry.register_session(copies, {'peerToken': 'b'}, {'name': 'new', 'pid': 2, 'sessionId': 'new'})\n"
            "updated = registry.update_session(copies, {'status': 'x'}, 'old')\n"
            "removed = registry.remove_session(copies, 'old')\n"
            "meta = json.load(open(copies[0][0]))\n"
            "print(json.dumps({'updated': updated, 'removed': removed, 'name': meta['name'], 'status': meta.get('status'),\n"
            "                  'key': os.path.exists(copies[0][1])}))\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual((out["updated"], out["removed"], out["name"], out["status"], out["key"]), (False, [], "new", None, True))

    def test_a_writer_waits_for_the_registry_lock(self):
        done = self._py(
            "import json, os, threading, time\n"
            "from agent_peer import registry\n"
            "copies = registry.session_paths(1, 'k.key', ('codex', 'L'))\n"
            "registry.ensure_sessions_dir(copies)\n"
            "registry.register_session(copies, {'peerToken': 'a'}, {'name': 'n', 'pid': 1, 'sessionId': 's'})\n"
            "holding = threading.Event()\n"
            "def hold():\n"
            "    with registry._registry_lock():\n"
            "        holding.set()\n"
            "        time.sleep(1.0)\n"
            "t = threading.Thread(target=hold); t.start(); holding.wait()\n"
            "start = time.monotonic()\n"
            "registry.update_session(copies, {'status': 'x'}, 's')\n"
            "print(json.dumps({'waited': time.monotonic() - start}))\n"
            "t.join()\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertGreater(json.loads(done.stdout)["waited"], 0.7)

    def test_a_registration_that_fails_half_way_leaves_nothing_behind(self):
        os.makedirs(os.path.join(self.home, ".claude"))
        done = self._py(
            "import json, os\n"
            "from agent_peer import listener, registry\n"
            "real = registry.register_session\n"
            "def half(copies, key_data, meta):\n"
            "    real(copies[:1], key_data, meta)\n"
            "    raise OSError('disk full')\n"
            "listener.register_session = half\n"
            "l = listener.PeerListener(name='zz-g-fail', codex_thread_id='T')\n"
            "try:\n"
            "    l.run()\n"
            "except OSError:\n"
            "    pass\n"
            "left = [p for c in l.copies for p in c if os.path.exists(p)]\n"
            "left += [p for p in (l.sock_path, l.symlink_path) if os.path.lexists(p)]\n"
            "print(json.dumps(left))\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), [])

    def test_a_second_process_waits_for_the_registry_lock(self):
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import time\nfrom agent_peer import registry\n"
             "with registry._registry_lock():\n    print('held', flush=True)\n    time.sleep(1.2)\n"],
            cwd=REPO_ROOT, env=isolated_env(self.home), stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        done = self._py(
            "import json, time\nfrom agent_peer import registry\n"
            "start = time.monotonic()\nregistry.update_session([('/nonexistent.json', None)], {}, 's')\n"
            "print(json.dumps({'waited': time.monotonic() - start}))\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertGreater(json.loads(done.stdout)["waited"], 0.6)

    def test_cleanup_inside_the_lock_does_not_wait_for_itself(self):
        done = self._py(
            "import json, time\nfrom agent_peer import registry\n"
            "copies = [('/nonexistent.json', None)]\n"
            "with registry._registry_lock():\n"
            "    start = time.monotonic()\n"
            "    registry.remove_session(copies, 's')\n"
            "    registry.update_session(copies, {}, 's')\n"
            "print(json.dumps({'waited': time.monotonic() - start}))\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertLess(json.loads(done.stdout)["waited"], 0.5)

    def test_a_failure_while_writing_the_second_copy_leaves_nothing_behind(self):
        os.makedirs(os.path.join(self.home, ".claude"))
        done = self._py(
            "import json, os\n"
            "from agent_peer import listener, registry\n"
            "real = registry.atomic_write_json\n"
            "calls = []\n"
            "def flaky(path, data):\n"
            "    calls.append(path)\n"
            "    if len(calls) == 2:\n"
            "        raise OSError('disk full')\n"
            "    real(path, data)\n"
            "registry.atomic_write_json = flaky\n"
            "l = listener.PeerListener(name='zz-g-fail2', codex_thread_id='T2')\n"
            "try:\n"
            "    l.run()\n"
            "except OSError:\n"
            "    pass\n"
            "left = [p for c in l.copies for p in c if os.path.exists(p)]\n"
            "left += [p for p in (l.sock_path, l.symlink_path) if os.path.lexists(p)]\n"
            "print(json.dumps({'calls': len(calls), 'left': left}))\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual((out["calls"], out["left"]), (2, []))

    def test_an_orphan_key_is_removed_even_when_an_owner_is_named(self):
        done = self._py(
            "import json, os\nfrom agent_peer import registry\n"
            "copies = registry.session_paths(1, 'k.key', ('codex', 'O'))\n"
            "registry.ensure_sessions_dir(copies)\n"
            "open(copies[0][1], 'w').write('{}')\n"
            "registry.remove_session(copies, 's')\n"
            "print(json.dumps(os.path.exists(copies[0][1])))\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(json.loads(done.stdout))

    def test_prune_from_a_stale_snapshot_spares_the_replacements_socket(self):
        done = self._py(
            "import json, os, types\n"
            "from agent_peer import cli, registry\n"
            "socks = os.environ['AGENT_PEER_SOCKET_DIR']\n"
            "copies = registry.session_paths(7, 'k.key', ('codex', 'S'))\n"
            "registry.ensure_sessions_dir(copies)\n"
            "sock = os.path.join(socks, '7.sock'); link = os.path.join(socks, 'zz-g-stale.sock')\n"
            "open(sock, 'w').close(); os.symlink(sock, link)\n"
            "registry.register_session(copies, {'peerToken': 'b'}, {'name': 'zz-g-stale', 'pid': 7, 'sessionId': 'newer'})\n"
            "stale = {'name': 'zz-g-stale', 'pid': 7, 'alive': False, 'sessionId': 'older', 'copies': copies,\n"
            "         'messagingSocketPath': sock}\n"
            "cli.get_active_sessions = lambda: [stale, {'name': 'other', 'pid': 8, 'alive': True, 'copies': []}]\n"
            "cli.cmd_prune(types.SimpleNamespace(force=False))\n"
            "print(json.dumps({'entry': os.path.exists(copies[0][0]), 'sock': os.path.exists(sock), 'link': os.path.islink(link)}))\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1]), {"entry": True, "sock": True, "link": True})

    def test_prune_spares_the_transport_when_the_pid_came_back_to_life(self):
        done = self._py(
            "import json, os, types\n"
            "from agent_peer import cli\n"
            "socks = os.environ['AGENT_PEER_SOCKET_DIR']\n"
            "sock = os.path.join(socks, f'{os.getpid()}.sock'); link = os.path.join(socks, 'zz-g-reuse.sock')\n"
            "open(sock, 'w').close(); os.symlink(sock, link)\n"
            "stale = {'name': 'zz-g-reuse', 'pid': os.getpid(), 'alive': False, 'sessionId': 'older', 'copies': [],\n"
            "         'messagingSocketPath': sock}\n"
            "cli.get_active_sessions = lambda: [stale, {'name': 'other', 'pid': 1, 'alive': True, 'copies': []}]\n"
            "cli.cmd_prune(types.SimpleNamespace(force=False))\n"
            "print(json.dumps({'sock': os.path.exists(sock), 'link': os.path.islink(link)}))\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1]), {"sock": True, "link": True})

    def test_cleanup_during_lock_acquisition_does_not_wait(self):
        done = self._py(
            "import json, time\nfrom agent_peer import registry\n"
            "def acquiring(path, deadline):\n"
            "    start = time.monotonic()\n"
            "    registry.remove_session([('/nonexistent.json', None)], 's')\n"
            "    acquiring.waited = time.monotonic() - start\n"
            "    return None, None\n"
            "registry._acquire_file_lock = acquiring\n"
            "with registry._registry_lock():\n    pass\n"
            "print(json.dumps({'waited': acquiring.waited}))\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertLess(json.loads(done.stdout)["waited"], 0.5)

    def test_an_interrupted_acquisition_leaks_no_descriptor_and_the_lock_stays_usable(self):
        done = self._py(
            "import json, os\nfrom agent_peer import registry\n"
            "if registry.fcntl is None:\n    print(json.dumps({'skip': True})); raise SystemExit\n"
            "real = registry.fcntl.flock\n"
            "def interrupted(fd, op):\n    raise KeyboardInterrupt\n"
            "before = len(os.listdir('/dev/fd'))\n"
            "registry.fcntl.flock = interrupted\n"
            "try:\n    with registry._registry_lock():\n        pass\n"
            "except KeyboardInterrupt:\n    pass\n"
            "registry.fcntl.flock = real\n"
            "ok = []\n"
            "with registry._registry_lock():\n    ok.append(registry._lock_depth)\n"
            "print(json.dumps({'leaked': len(os.listdir('/dev/fd')) - before, 'depth_after': registry._lock_depth, 'reusable': ok}))\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        if out.get("skip"):
            self.skipTest("no fcntl on this platform")
        self.assertEqual((out["leaked"], out["depth_after"], out["reusable"]), (0, 0, [1]))

    def test_the_lock_gives_up_after_its_timeout_and_proceeds(self):
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import time\nfrom agent_peer import registry\n"
             "with registry._registry_lock():\n    print('held', flush=True)\n    time.sleep(2.0)\n"],
            cwd=REPO_ROOT, env=isolated_env(self.home), stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        done = self._py(
            "import json, time\nfrom agent_peer import registry\n"
            "start = time.monotonic()\nran = []\n"
            "with registry._registry_lock(timeout=0.3):\n    ran.append(True)\n"
            "print(json.dumps({'waited': time.monotonic() - start, 'ran': ran}))\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(out["ran"], [True])
        self.assertTrue(0.2 < out["waited"] < 1.5, out)

    def test_prune_leaves_a_name_link_that_belongs_to_a_replacement(self):
        os.makedirs(self.own)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        socks = socket_dir(self.home)
        with open(os.path.join(self.own, "pid.dead.json"), "w") as f:
            json.dump({"name": "zz-g-same", "pid": dead.pid, "sessionId": "dead",
                       "messagingSocketPath": os.path.join(socks, f"{dead.pid}.sock")}, f)
        replacement = os.path.join(socks, "zz-g-same.sock")
        os.symlink(os.path.join(socks, "live-replacement.sock"), replacement)
        self.assertEqual(run_cli(["prune"], self.home).returncode, 0)
        self.assertEqual(os.listdir(self.own), [])
        self.assertTrue(os.path.islink(replacement))


if __name__ == "__main__":
    unittest.main()
