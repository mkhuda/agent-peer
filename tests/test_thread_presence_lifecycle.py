"""Presence lifecycle: peek-vs-room intent, live-reader suppression, and
mesh-wide mention knock without auto-enrollment (docs/tasks/0029, Phase 4).

End-to-end through the real CLI with an isolated $HOME, mirroring
test_thread_push.py: native targets are dummy UDS servers fronted by
crafted registry entries.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest

from tests.helpers import isolated_home, run_cli, spawn_cli, stop_cli, wait_until

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DummyNativeServer:
    """A fake native-Claude UDS endpoint: accepts connections, records raw
    bytes. Proves a fanout push actually went out over socket transport."""

    def __init__(self, sock_path):
        self.received = []
        self._stop = threading.Event()
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(sock_path)
        self._sock.listen(5)
        self._sock.settimeout(0.2)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                chunks = []
                try:
                    while True:
                        data = conn.recv(4096)
                        if not data:
                            break
                        chunks.append(data)
                except socket.timeout:
                    pass
                self.received.append(b"".join(chunks))

    def close(self):
        self._stop.set()
        self._thread.join(timeout=3)
        self._sock.close()

    def text(self):
        return b"".join(self.received).decode("utf-8", errors="replace")


def _presence(home, thread_id):
    path = os.path.join(home, ".agent-peer", "threads", f"{thread_id}.presence.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except Exception:
            return {}


def _write_presence(home, thread_id, presence):
    path = os.path.join(home, ".agent-peer", "threads", f"{thread_id}.presence.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(presence, fh)


class PresenceLifecycleTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            stop_cli(proc)
        self._home_cm.__exit__(None, None, None)

    def _native_session(self, name, status="idle"):
        """Craft a native-Claude-style registry entry (not managed) whose
        socket is a dummy server. Returns the server (caller must close)."""
        pid = os.getpid()  # alive by definition
        sock_path = os.path.join(self.home, f"{name}.sock")
        server = DummyNativeServer(sock_path)
        sessions = os.path.join(self.home, ".claude", "sessions")
        os.makedirs(sessions, exist_ok=True)
        with open(os.path.join(sessions, f"{pid}.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "name": name,
                    "status": status,
                    "managedByAgentPeer": False,
                    "messagingSocketPath": sock_path,
                },
                fh,
            )
        # resolve_session reads the token from the key file, not the json.
        with open(os.path.join(sessions, f"{pid}.test.key"), "w", encoding="utf-8") as fh:
            json.dump({"peerToken": "test-token"}, fh)
        return server

    def _post(self, thread_id, sender, message):
        proc = run_cli(["send", "--thread", thread_id, "--sender", sender, message], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_peek_with_timeout_does_not_arm_push(self):
        server = self._native_session("pat")
        try:
            run_cli(["thread", "t1", "--name", "pat", "--timeout", "1"], self.home)
            self.assertTrue(_presence(self.home, "t1")["pat"].get("left"), "a bounded wait is a peek")
            self._post("t1", "bob", "general banter")
            time.sleep(1)  # fanout runs inside the post call; absence after is final
            self.assertEqual(server.text(), "", "peek must not arm 5 minutes of banter push")
        finally:
            server.close()

    def test_indefinite_wait_arms_room_push(self):
        server = self._native_session("ina")
        try:
            waiter = spawn_cli(["thread", "t2", "--name", "ina"], self.home)
            self.procs.append(waiter)
            self.assertTrue(
                wait_until(lambda: _presence(self.home, "t2").get("ina", {}).get("left") is False, timeout=5),
                "indefinite wait must record active room presence",
            )
            stop_cli(waiter)
            self.procs.remove(waiter)
            self._post("t2", "bob", "general banter")
            self.assertTrue(
                wait_until(lambda: "general banter" in server.text(), timeout=5),
                "dead waiter with armed presence must get the backstop push",
            )
        finally:
            server.close()

    def test_join_preserves_active_presence(self):
        script = (
            f"import sys; sys.path.insert(0, {REPO_ROOT!r}); "
            "from agent_peer.join import run_join; run_join('t3', 'foreman')"
        )
        env = dict(os.environ, HOME=self.home)
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            input="\n",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("foreman", _presence(self.home, "t3"))
        self.assertFalse(
            _presence(self.home, "t3")["foreman"].get("left"), "interactive join is active presence, never a peek"
        )

    def test_live_waiter_suppresses_socket_push(self):
        server = self._native_session("liv")
        try:
            waiter = spawn_cli(["thread", "t4", "--name", "liv"], self.home)
            self.procs.append(waiter)
            self.assertTrue(
                wait_until(lambda: "liv" in _presence(self.home, "t4"), timeout=5),
                "waiter never touched presence",
            )
            self.assertFalse(_presence(self.home, "t4")["liv"].get("left"))
            self._post("t4", "bob", "general banter")
            time.sleep(1)  # fanout runs inside the post call; absence after is final
            self.assertEqual(server.text(), "", "live reader already sees the poll - no socket ping")
            stop_cli(waiter)
            self.procs.remove(waiter)
            self._post("t4", "bob", "second wave")
            self.assertTrue(
                wait_until(lambda: "second wave" in server.text(), timeout=5),
                "once the reader exits, the same presence must get the backstop",
            )
        finally:
            server.close()

    def test_dead_waiter_triggers_wake_push(self):
        server = self._native_session("zed")
        try:
            _write_presence(self.home, "t5", {"zed": {"pid": 424242, "last_seen": time.time()}})
            self._post("t5", "bob", "wake up")
            self.assertTrue(
                wait_until(lambda: "[thread: t5 #1 from bob]: wake up" in server.text(), timeout=5),
                "dead waiter with fresh presence must get the wake push",
            )
        finally:
            server.close()

    def test_mesh_wide_mention_one_shot_without_auto_enroll(self):
        server = self._native_session("zoe")
        try:
            self.assertNotIn("zoe", _presence(self.home, "t6"))
            self._post("t6", "bob", "@zoe come here please")
            self.assertTrue(
                wait_until(lambda: "@zoe come here please" in server.text(), timeout=5),
                "explicit @mention must knock a session that never joined",
            )
            self.assertNotIn("zoe", _presence(self.home, "t6"), "knock must not enroll")
            self._post("t6", "bob", "more banter")
            time.sleep(1)  # fanout runs inside the post call; absence after is final
            self.assertEqual(server.text().count("[thread:"), 1, "one-shot: banter after must not push")
        finally:
            server.close()

    def test_mesh_mention_matches_exact_name_only(self):
        server = self._native_session("sam-2")
        try:
            self._post("t7", "bob", "@sam hi")
            time.sleep(1)  # fanout runs inside the post call; absence after is final
            self.assertEqual(server.text(), "", "@sam must not summon a session named sam-2")
            self._post("t7", "bob", "@sam-2 hi")
            self.assertTrue(
                wait_until(lambda: "@sam-2 hi" in server.text(), timeout=5),
                "exact @sam-2 must knock",
            )
        finally:
            server.close()

    def test_pid_reuse_resilience(self):
        server = self._native_session("old")
        try:
            # Stale timestamp, but the pid belongs to this very test process:
            # alive, yet must not resurrect the entry.
            _write_presence(
                self.home, "t8", {"old": {"pid": os.getpid(), "last_seen": time.time() - 600}}
            )
            self._post("t8", "bob", "general banter")
            time.sleep(1)  # fanout runs inside the post call; absence after is final
            self.assertEqual(server.text(), "", "stale entry must stay skipped despite a live pid")
        finally:
            server.close()


    def test_busy_gating_uses_exact_mention_token(self):
        server = self._native_session("sam", status="busy")
        try:
            _write_presence(self.home, "t9", {"sam": {"pid": 424242, "last_seen": time.time()}})
            self._post("t9", "bob", "@sam-2 hi")
            time.sleep(1)  # fanout runs inside the post call; absence after is final
            self.assertEqual(server.text(), "", "@sam-2 must not wake a busy sam")
            self._post("t9", "bob", "@sam hi")
            self.assertTrue(
                wait_until(lambda: "@sam hi" in server.text(), timeout=5),
                "exact @sam must push through busy gating",
            )
        finally:
            server.close()

    def test_soft_leave_knock_uses_exact_mention_token(self):
        server = self._native_session("sal")
        try:
            _write_presence(
                self.home, "t10", {"sal": {"pid": 424242, "last_seen": time.time(), "left": True}}
            )
            self._post("t10", "bob", "@sal-2 hi")
            time.sleep(1)  # fanout runs inside the post call; absence after is final
            self.assertEqual(server.text(), "", "@sal-2 must not knock a left sal")
            self.assertTrue(_presence(self.home, "t10")["sal"].get("left"), "must stay left")
            self._post("t10", "bob", "@sal hi")
            self.assertTrue(
                wait_until(lambda: "@sal hi" in server.text(), timeout=5),
                "exact @sal must knock through soft-leave",
            )
            self.assertFalse(_presence(self.home, "t10")["sal"].get("left"), "knock must restore")
        finally:
            server.close()


class MentionTokenTest(unittest.TestCase):
    """_mention_tokens is pure string work: pin the token shape here."""

    def test_matrix(self):
        from agent_peer.thread import _mention_tokens

        self.assertEqual(_mention_tokens("@zoe come"), {"zoe"})
        self.assertEqual(_mention_tokens("@sam-2 hi @zoe"), {"sam-2", "zoe"})
        self.assertEqual(_mention_tokens("no mentions"), set())
        self.assertEqual(_mention_tokens("@all stop"), {"all"})
        # Trailing punctuation is not part of the name.
        self.assertEqual(_mention_tokens("hi @zoe, welcome"), {"zoe"})

    def test_mentions_matrix(self):
        from agent_peer.thread import _mentions

        self.assertTrue(_mentions("@sam hi", "sam"))
        self.assertFalse(_mentions("@sam-2 hi", "sam"))
        self.assertTrue(_mentions("hey @all", "whoever"))
        self.assertTrue(_mentions("[stop] now", "whoever"))
        self.assertFalse(_mentions("general banter", "sam"))


if __name__ == "__main__":
    unittest.main()
