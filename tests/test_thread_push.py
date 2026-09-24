"""Native push fanout for shared threads (docs/tasks/0028, Phase 5).

End-to-end through real listeners with an isolated $HOME: a post must
nudge active participants' sockets, skip the sender/left/busy-unmentioned,
and never fail the poster on dead targets.
"""

import glob
import json
import os
import socket
import subprocess
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


def _inbox_contents(home, name):
    path = os.path.join(home, ".agent-peer", "inboxes", f"{name}.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line).get("content", ""))
                except Exception:
                    continue
    return out


def _presence(home, thread_id):
    path = os.path.join(home, ".agent-peer", "threads", f"{thread_id}.presence.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except Exception:
            return {}


class ThreadPushTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            stop_cli(proc)
        self._home_cm.__exit__(None, None, None)

    def _listen(self, name):
        proc = spawn_cli(["listen", "--name", name], self.home)
        self.procs.append(proc)
        sessions = os.path.join(self.home, ".claude", "sessions", "*.json")
        self.assertTrue(
            wait_until(lambda: glob.glob(sessions), timeout=5),
            f"listener {name} never registered",
        )
        return proc

    def _waiter(self, thread_id, name, timeout=15):
        proc = spawn_cli(["thread", thread_id, "--name", name, "--timeout", str(timeout)], self.home)
        self.procs.append(proc)
        self.assertTrue(
            wait_until(lambda: name in _presence(self.home, thread_id), timeout=5),
            f"{name} never touched presence",
        )
        return proc

    def _post(self, thread_id, sender, message):
        proc = run_cli(["send", "--thread", thread_id, "--sender", sender, message], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def _native_session(self, name, status="idle", pid=None):
        """Craft a native-Claude-style registry entry (not managed) whose
        socket is a dummy server. Returns the server (caller must close).
        `pid` defaults to this test process's own (alive by definition) -
        pass a different real, alive pid when a test needs two simultaneous
        native sessions, since one pid can only own one session file."""
        pid = pid or os.getpid()
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

    def _touch_presence(self, thread_id, name):
        path = os.path.join(self.home, ".agent-peer", "threads", f"{thread_id}.presence.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, encoding="utf-8") as fh:
                presence = json.load(fh)
        except Exception:
            presence = {}
        presence[name] = {"pid": 424242, "last_seen": time.time()}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(presence, fh)

    def test_active_native_never_pushed_poll_only(self):
        # Intercom doctrine (test-1 consensus #344-351): an active member
        # reads the room stream via poll; the socket never speaks inside
        # the room, not even on @mention.
        server = self._native_session("nat")
        try:
            self._touch_presence("t1", "nat")
            self._post("t1", "bob", "hello team")
            time.sleep(1)  # fanout runs inside the post call; absence after is final
            self.assertEqual(server.text(), "", "active banter must not push")
            self._post("t1", "bob", "@nat are you there")
            time.sleep(1)
            self.assertEqual(server.text(), "", "active mention must not push either")
        finally:
            server.close()

    def test_managed_session_skipped_no_dupe(self):
        self._listen("anna")
        self._waiter("t1", "anna")
        self._post("t1", "bob", "hello team")
        time.sleep(1)  # fanout runs inside the post call; absence after is final
        self.assertEqual(_inbox_contents(self.home, "anna"), [], "managed listener must not get an inbox dupe")

    def test_self_push_suppressed(self):
        self._listen("solo")
        self._waiter("t1", "solo")
        self._post("t1", "solo", "talking to myself")
        time.sleep(1)  # fanout runs inside the post call; absence after is final
        self.assertEqual(_inbox_contents(self.home, "solo"), [])

    def test_leave_stops_push(self):
        self._listen("leah")
        self._waiter("t1", "leah")
        left = run_cli(["thread", "t1", "--name", "leah", "--leave"], self.home)
        self.assertEqual(left.returncode, 0, left.stderr)
        self.assertTrue(_presence(self.home, "t1")["leah"].get("left"), "leave must mark, not delete")
        self._post("t1", "bob", "are you there")
        time.sleep(1)
        self.assertEqual(_inbox_contents(self.home, "leah"), [])

    def test_left_managed_listener_gets_knock_in_inbox(self):
        # A left managed member is not polling the room, so the knock is
        # their only delivery - it must land in the listener inbox.
        self._listen("kate")
        waiter = self._waiter("t1", "kate")
        stop_cli(waiter)
        self.procs.remove(waiter)
        left = run_cli(["thread", "t1", "--name", "kate", "--leave"], self.home)
        self.assertEqual(left.returncode, 0, left.stderr)
        self._post("t1", "bob", "@kate urgent")
        self.assertTrue(
            wait_until(
                lambda: any("@kate urgent" in c for c in _inbox_contents(self.home, "kate")),
                timeout=5,
            ),
            "left + mention must knock the managed inbox",
        )

    def test_soft_leave_knock_no_restore(self):
        # 0030 doorbell presence: the knock rings but never enrolls - left
        # stays left, so consecutive mentions keep knocking reliably.
        server = self._native_session("ned")
        try:
            run_cli(["thread", "t1", "--name", "ned", "--timeout", "1"], self.home)
            run_cli(["thread", "t1", "--name", "ned", "--leave"], self.home)
            self.assertTrue(_presence(self.home, "t1")["ned"].get("left"))
            self._post("t1", "bob", "general banter")
            time.sleep(1)
            self.assertEqual(server.text(), "", "banter must not knock a left participant")
            self._post("t1", "bob", "@ned urgent")
            self.assertTrue(
                wait_until(lambda: "@ned urgent" in server.text(), timeout=5),
                "mention must knock through soft-leave",
            )
            self.assertTrue(
                _presence(self.home, "t1")["ned"].get("left"), "knock must not restore presence"
            )
            self._post("t1", "bob", "@ned second call")
            self.assertTrue(
                wait_until(lambda: "@ned second call" in server.text(), timeout=5),
                "consecutive mention must knock again, no parked-active trap",
            )
            self.assertTrue(_presence(self.home, "t1")["ned"].get("left"), "still left after two knocks")
        finally:
            server.close()

    def test_push_frame_includes_reply_routing_instruction(self):
        # 0034: a session that never read the skill must still be able to
        # tell, from the delivered frame alone, the exact command to reply
        # into the thread rather than answering back 1-to-1.
        server = self._native_session("ren")
        try:
            run_cli(["thread", "t1", "--name", "ren", "--timeout", "1"], self.home)
            run_cli(["thread", "t1", "--name", "ren", "--leave"], self.home)
            self._post("t1", "bob", "@ren check this out")
            self.assertTrue(
                wait_until(lambda: "@ren check this out" in server.text(), timeout=5),
                "mention must knock through",
            )
            # server.text() is the raw JSON-over-the-wire frame - quotes in
            # the reply command are JSON-escaped there.
            self.assertIn(
                'agent-peer send --thread t1 \\"...\\"',
                server.text(),
                "frame must carry the exact reply command, not just the thread id",
            )
        finally:
            server.close()

    def test_follow_all_receives_banter_without_mention(self):
        server = self._native_session("fin")
        try:
            run_cli(["thread", "t1", "--name", "fin", "--timeout", "1", "--follow"], self.home)
            self.assertTrue(_presence(self.home, "t1")["fin"].get("follow_all"))
            self._post("t1", "bob", "just chatting, no mention here")
            self.assertTrue(
                wait_until(lambda: "just chatting" in server.text(), timeout=5),
                "follow-all must knock on ordinary banter, no @mention needed",
            )
        finally:
            server.close()

    def test_follow_all_persists_across_a_plain_repeek(self):
        # A standing opt-in, not a per-call state like `left`: a later peek
        # without --follow must not silently drop it - Codex re-peeks
        # constantly and shouldn't have to re-pass --follow every time.
        run_cli(["thread", "t1", "--name", "kim", "--timeout", "1", "--follow"], self.home)
        self.assertTrue(_presence(self.home, "t1")["kim"].get("follow_all"))
        run_cli(["thread", "t1", "--name", "kim", "--timeout", "1"], self.home)
        self.assertTrue(
            _presence(self.home, "t1")["kim"].get("follow_all"), "a plain re-peek must not clear follow_all"
        )

    def test_follow_all_does_not_push_own_post(self):
        server = self._native_session("gia")
        try:
            run_cli(["thread", "t1", "--name", "gia", "--timeout", "1", "--follow"], self.home)
            self._post("t1", "gia", "talking to myself")
            time.sleep(1)  # fanout runs inside the post call; absence after is final
            self.assertEqual(server.text(), "", "follow-all must never push a participant their own post")
        finally:
            server.close()

    def test_follow_all_sender_match_is_exact_not_substring(self):
        # Same collision class already fixed for @mention (sam vs sam-2) -
        # a follow-all participant named codex-7284 must not be treated as
        # having posted (and thus self-suppressed) by codex-7284-2's post.
        server = self._native_session("codex-7284")
        try:
            run_cli(["thread", "t1", "--name", "codex-7284", "--timeout", "1", "--follow"], self.home)
            self._post("t1", "codex-7284-2", "hello from a similarly-named session")
            self.assertTrue(
                wait_until(lambda: "hello from a similarly-named" in server.text(), timeout=5),
                "a post from a different, similarly-named sender must still knock",
            )
        finally:
            server.close()

    def test_leave_clears_follow_all(self):
        run_cli(["thread", "t1", "--name", "hal", "--timeout", "1", "--follow"], self.home)
        self.assertTrue(_presence(self.home, "t1")["hal"].get("follow_all"))
        run_cli(["thread", "t1", "--name", "hal", "--leave"], self.home)
        self.assertFalse(_presence(self.home, "t1")["hal"].get("follow_all"), "--leave must clear follow_all too")

    def test_follow_all_does_not_affect_a_session_never_in_this_thread(self):
        # 0035 invariant #2: scoped to participants who have touched THIS
        # thread's presence at least once - an unrelated mesh session is
        # not swept up just because someone else opted into follow-all.
        # Two simultaneous native sessions need two distinct, genuinely
        # alive pids - one process can only register one session file.
        bystander_proc = subprocess.Popen(["sleep", "5"])
        follow_server = self._native_session("ivy")
        bystander_server = self._native_session("jude", pid=bystander_proc.pid)
        try:
            run_cli(["thread", "t1", "--name", "ivy", "--timeout", "1", "--follow"], self.home)
            self._post("t1", "bob", "no mention of anyone")
            self.assertTrue(
                wait_until(lambda: "no mention of anyone" in follow_server.text(), timeout=5),
                "follow-all participant must still get it",
            )
            time.sleep(1)
            self.assertEqual(
                bystander_server.text(), "", "a session that never touched this thread must stay untouched"
            )
        finally:
            follow_server.close()
            bystander_server.close()
            bystander_proc.terminate()
            bystander_proc.wait(timeout=5)

    def test_rejoin_replays_backlog(self):
        run_cli(["thread", "t1", "--name", "rick", "--timeout", "1"], self.home)
        run_cli(["thread", "t1", "--name", "rick", "--leave"], self.home)
        self._post("t1", "bob", "first")
        self._post("t1", "bob", "second")
        rejoined = run_cli(["thread", "t1", "--name", "rick", "--timeout", "5"], self.home)
        self.assertEqual(rejoined.returncode, 0, rejoined.stdout)
        self.assertIn("#1", rejoined.stdout)
        self.assertIn("#2", rejoined.stdout)
        self.assertIn("rick", _presence(self.home, "t1"))
        # 0029 timeout-as-intent: a bounded rejoin is a peek, so it replays
        # the backlog but stays gated; only an indefinite wait clears left.
        self.assertTrue(_presence(self.home, "t1")["rick"].get("left"), "timeout rejoin stays gated")
        indefinite = spawn_cli(["thread", "t1", "--name", "rick"], self.home)
        self.procs.append(indefinite)
        try:
            self.assertTrue(
                wait_until(lambda: _presence(self.home, "t1").get("rick", {}).get("left") is False, timeout=5),
                "indefinite rejoin must clear left",
            )
        finally:
            stop_cli(indefinite)
            self.procs.remove(indefinite)

    def test_dead_presence_never_fails_poster(self):
        path = os.path.join(self.home, ".agent-peer", "threads", "t1.presence.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"ghost": {"pid": 999999, "last_seen": time.time()}}, fh)
        t0 = time.time()
        self._post("t1", "bob", "hello ghosts")
        self.assertLess(time.time() - t0, 5, "dead target must not stall the post")
        log = os.path.join(self.home, ".agent-peer", "threads", "t1.jsonl")
        with open(log, encoding="utf-8") as fh:
            self.assertIn("hello ghosts", fh.read())

    def test_busy_active_silent_left_knock_rings(self):
        # Busy no longer gates anything for actives (they are poll-only);
        # the knock path is left + mention, regardless of busy status.
        server = self._native_session("beth", status="busy")
        try:
            self._touch_presence("t1", "beth")
            self._post("t1", "bob", "@beth urgent")
            time.sleep(1)  # fanout runs inside the post call; absence after is final
            self.assertEqual(server.text(), "", "active mention must not push, busy or not")
            left = run_cli(["thread", "t1", "--name", "beth", "--leave"], self.home)
            self.assertEqual(left.returncode, 0, left.stderr)
            self._post("t1", "bob", "@beth-2 hi")
            time.sleep(1)
            self.assertEqual(server.text(), "", "@beth-2 must not knock a left beth")
            self._post("t1", "bob", "@beth urgent")
            self.assertTrue(
                wait_until(lambda: "@beth urgent" in server.text(), timeout=5),
                "left + mention must knock even when busy",
            )
        finally:
            server.close()

    def test_skill_docs_routing_convention(self):
        for harness in ("agy", "claude", "codex", "muse", "opencode", "pi"):
            path = os.path.join(REPO_ROOT, "skills", harness, "SKILL.md")
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("[thread: <id>", text, harness)
            self.assertIn("MUST be answered", text, harness)


class AutoNameFixOneTest(unittest.TestCase):
    """0028 fix #1: auto_session_name reuses the registered name when the
    detected harness pid has a live session (in-process with patched
    bindings; protocol binds DIRS at import so subprocess is unnecessary)."""

    def setUp(self):
        import tempfile

        from agent_peer import protocol, registry

        self.protocol = protocol
        self.registry = registry
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-peer-sessions-")
        self._orig_sessions = protocol.SESSIONS_DIR
        self._orig_detect = protocol.detect_harness_identity
        # Both modules bound SESSIONS_DIR at import; patch both.
        protocol.SESSIONS_DIR = self._tmp.name
        registry.SESSIONS_DIR = self._tmp.name

    def tearDown(self):
        self.protocol.SESSIONS_DIR = self._orig_sessions
        self.registry.SESSIONS_DIR = self._orig_sessions
        self.protocol.detect_harness_identity = self._orig_detect
        self._tmp.cleanup()

    def _register(self, pid, name):
        with open(os.path.join(self._tmp.name, f"{pid}.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": name}, fh)

    def test_registered_live_pid_returns_official_name(self):
        self._register(os.getpid(), "agent-peer-e4")
        self.protocol.detect_harness_identity = lambda max_depth=6: ("claude", os.getpid())
        self.assertEqual(self.protocol.auto_session_name(), "agent-peer-e4")

    def test_dead_registration_falls_back_to_invented(self):
        self._register(999999, "agent-peer-e4")
        self.protocol.detect_harness_identity = lambda max_depth=6: ("claude", 999999)
        self.assertEqual(self.protocol.auto_session_name(), "claude-999999")

    def test_unregistered_falls_back_to_invented(self):
        self.protocol.detect_harness_identity = lambda max_depth=6: ("claude", 999998)
        self.assertEqual(self.protocol.auto_session_name(), "claude-999998")


if __name__ == "__main__":
    unittest.main()
