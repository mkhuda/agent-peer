"""
The peer auth handshake must actually be enforced.

Regression test for the auth-bypass bug found this session: `authenticated`
was computed in listener.py's handle_client() but never checked before a
frame got processed, so any connection could inject a message without ever
sending a valid (or any) auth frame. See .dev/HANDOFF.md.
"""

import glob
import json
import os
import socket
import time
import unittest

from tests.helpers import isolated_home, run_cli, spawn_cli, stop_cli, wait_until

SESSION = "auth-test"


def _sessions_dir(home):
    return os.path.join(home, ".claude", "sessions")


def _session_file(home):
    files = glob.glob(os.path.join(_sessions_dir(home), "*.json"))
    return files[0] if files else None


def _inbox_messages(home):
    path = os.path.join(home, ".agent-peer", "inboxes", f"{SESSION}.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _send_frames(sock_path, frames, settle=0.3):
    """Connect once and send each already-JSON-encoded frame in order."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(3)
    try:
        client.connect(sock_path)
        for frame in frames:
            client.sendall((json.dumps(frame) + "\n").encode())
            time.sleep(0.05)
        time.sleep(settle)
    finally:
        client.close()


class AuthGateTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()
        self.listener = spawn_cli(["listen", "--name", SESSION], self.home)
        self.assertTrue(
            wait_until(lambda: _session_file(self.home), timeout=5),
            "listener never registered a session",
        )
        with open(_session_file(self.home)) as f:
            meta = json.load(f)
        self.sock_path = meta["messagingSocketPath"]

        key_files = glob.glob(os.path.join(_sessions_dir(self.home), "*.key"))
        with open(key_files[0]) as f:
            self.real_token = json.load(f)["peerToken"]

    def tearDown(self):
        stop_cli(self.listener)
        self._home_cm.__exit__(None, None, None)

    def test_frame_without_any_auth_is_rejected(self):
        _send_frames(self.sock_path, [
            {"type": "user", "priority": "now", "from": "attacker",
             "message": {"content": "no auth at all"}},
        ])
        self.assertEqual(_inbox_messages(self.home), [])

    def test_frame_with_wrong_token_is_rejected(self):
        _send_frames(self.sock_path, [
            {"type": "auth", "token": "definitely-not-the-real-token"},
            {"type": "user", "priority": "now", "from": "attacker",
             "message": {"content": "wrong token"}},
        ])
        self.assertEqual(_inbox_messages(self.home), [])

    def test_frame_with_correct_token_is_accepted(self):
        _send_frames(self.sock_path, [
            {"type": "auth", "token": self.real_token},
            {"type": "user", "priority": "now", "from": "legit-sender",
             "message": {"content": "hello"}},
        ])
        messages = _inbox_messages(self.home)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "hello")

    def test_real_send_command_still_works_end_to_end(self):
        """Sanity check that the fix didn't break the normal path."""
        result = run_cli(["send", SESSION, "via real send"], self.home)
        self.assertEqual(result.returncode, 0, result.stderr)
        messages = _inbox_messages(self.home)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "via real send")


if __name__ == "__main__":
    unittest.main()
