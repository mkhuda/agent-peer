"""A busy native Claude session gets its messages held and delivered, batched, once it is free.
Everything runs against a fake session registry and a fake socket inside an isolated HOME."""

import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor

from tests.helpers import RecordingEndpoint, isolated_home, run_cli, wait_until

FAST = {
    "AGENT_PEER_OUTBOX_POLL": "0.05",
    "AGENT_PEER_OUTBOX_DEBOUNCE": "0.2",
}
NAME = "target"


class FakeClaude:
    """A native-Claude registry entry whose endpoint records every user frame it receives."""

    def __init__(self, home, status="busy", name=NAME):
        self.home, self.name = home, name
        self.pid = os.getpid()
        self.sessions = os.path.join(home, ".claude", "sessions")
        os.makedirs(self.sessions, exist_ok=True)
        self.endpoint = RecordingEndpoint(os.path.join(home, f"{name}.sock"))
        self.sock_path = self.endpoint.address
        self.set_status(status)
        with open(os.path.join(self.sessions, f"{self.pid}.test.key"), "w", encoding="utf-8") as fh:
            json.dump({"peerToken": "test-token"}, fh)

    @property
    def frames(self):
        out = []
        for raw in list(self.endpoint.received):
            for line in raw.decode("utf-8", "replace").splitlines():
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                if frame.get("type") == "user":
                    out.append(frame)
        return out

    def set_status(self, status):
        path = os.path.join(self.sessions, f"{self.pid}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"name": self.name, "status": status, "managedByAgentPeer": False,
                       "messagingSocketPath": self.sock_path}, fh)
        os.replace(tmp, path)

    def remove(self):
        os.unlink(os.path.join(self.sessions, f"{self.pid}.json"))

    def texts(self):
        return [f["message"]["content"] for f in self.frames]

    def close(self):
        self.endpoint.close()


def held_files(home, pid=None):
    d = os.path.join(home, ".agent-peer", "outbox", str(pid or os.getpid()))
    return sorted(n for n in os.listdir(d) if n.endswith(".json")) if os.path.isdir(d) else []


class OutboxTest(unittest.TestCase):
    def setUp(self):
        self._cm = isolated_home()
        self.home = self._cm.__enter__()
        self.target = FakeClaude(self.home)

    def tearDown(self):
        run_cli(["outbox", NAME, "--cancel"], self.home)
        wait_until(self.lock_gone, timeout=5)  # Windows cannot delete a lock file a drainer still holds open
        self.target.close()
        self._cm.__exit__(None, None, None)

    def send(self, message, sender="a", *flags, env=None):
        return run_cli(["send", NAME, message, "--sender", sender, *flags], self.home, env_extra={**FAST, **(env or {})})

    def lock_gone(self):
        return not os.path.exists(os.path.join(self.home, ".agent-peer", "outbox", f"{os.getpid()}.lock"))

    def test_free_target_is_delivered_at_once_and_nothing_is_held(self):
        self.target.set_status("idle")
        p = self.send("hello")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Delivered", p.stdout)
        self.assertEqual(len(self.target.frames), 1)
        self.assertEqual(held_files(self.home), [])

    def test_status_symbols_do_not_break_send_on_a_console_that_cannot_print_them(self):
        self.target.set_status("idle")
        p = self.send("hello", "a", env={"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Delivered", p.stdout)
        self.target.set_status("busy")
        p = self.send("again", "b", env={"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Queued", p.stdout)

    def test_shell_status_counts_as_free(self):
        self.target.set_status("shell")
        self.assertIn("Delivered", self.send("hello").stdout)

    def test_busy_target_is_held_then_delivered_when_free(self):
        p = self.send("later please")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Queued", p.stdout)
        self.assertEqual(self.target.frames, [])
        self.assertEqual(len(held_files(self.home)), 1)
        self.target.set_status("idle")
        self.assertTrue(wait_until(lambda: self.target.frames, timeout=8), "never delivered after it became free")
        text = self.target.texts()[0]
        self.assertIn("[from a", text)
        self.assertIn("later please", text)
        self.assertIn("while you were busy", text)
        self.assertTrue(wait_until(lambda: not held_files(self.home), timeout=5))
        self.assertTrue(wait_until(self.lock_gone, timeout=5), "the drainer must exit once the queue is empty")

    def test_messages_from_several_senders_arrive_as_one_batch_in_order(self):
        for sender, text in (("alice", "one"), ("bob", "two"), ("carol", "three")):
            self.assertIn("Queued", self.send(text, sender).stdout)
        self.assertEqual(len(held_files(self.home)), 3)
        self.target.set_status("idle")
        self.assertTrue(wait_until(lambda: self.target.frames, timeout=8))
        self.assertTrue(wait_until(lambda: not held_files(self.home), timeout=5))
        self.assertEqual(len(self.target.frames), 1, "the three messages must arrive as a single delivery")
        body = self.target.texts()[0]
        self.assertIn("3 messages", body)
        self.assertLess(body.index("one"), body.index("two"))
        self.assertLess(body.index("two"), body.index("three"))
        for sender in ("alice", "bob", "carol"):
            self.assertIn(f"[from {sender}", body)

    def test_priority_orders_the_batch_and_fifo_holds_within_a_priority(self):
        self.send("L1", "a", "--priority", "later")
        self.send("N1", "b", "--priority", "now")
        self.send("X1", "c", "--priority", "next")
        self.send("N2", "d", "--priority", "now")
        self.target.set_status("idle")
        self.assertTrue(wait_until(lambda: self.target.frames, timeout=8))
        body = self.target.texts()[0]
        order = [body.index(t) for t in ("N1", "N2", "X1", "L1")]
        self.assertEqual(order, sorted(order))

    def test_stop_message_and_now_flag_are_never_held(self):
        p = self.send("[stop] drop what you are doing")
        self.assertIn("Delivered", p.stdout)
        p = self.send("urgent", "a", "--now")
        self.assertIn("Delivered", p.stdout)
        self.assertEqual(len(self.target.frames), 2)
        self.assertEqual(held_files(self.home), [])

    def test_identical_message_from_the_same_sender_is_held_once(self):
        self.send("same")
        self.send("same")
        self.assertEqual(len(held_files(self.home)), 1)

    def test_queue_limit_refuses_the_extra_message(self):
        env = {"AGENT_PEER_OUTBOX_MAX": "2"}
        self.send("m1", "a", env=env)
        self.send("m2", "b", env=env)
        p = self.send("m3", "c", env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("already has 2 messages waiting", p.stderr)
        self.assertEqual(len(held_files(self.home)), 2)

    def test_many_concurrent_senders_are_each_delivered_exactly_once(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: self.send(f"msg-{i}", f"s{i}"), range(8)))
        self.assertTrue(all(r.returncode == 0 for r in results), [r.stderr for r in results])
        self.assertEqual(len(held_files(self.home)), 8)
        self.target.set_status("idle")
        self.assertTrue(wait_until(lambda: self.target.frames and not held_files(self.home), timeout=10))
        joined = "\n".join(self.target.texts())
        for i in range(8):
            self.assertEqual(joined.count(f"msg-{i}"), 1, f"msg-{i} must be delivered exactly once")

    def test_outbox_command_lists_and_cancels(self):
        self.send("keep me out")
        p = run_cli(["outbox"], self.home)
        self.assertIn(f"{NAME} (PID {os.getpid()}): 1 held", p.stdout)
        self.assertIn("keep me out", p.stdout)
        p = run_cli(["outbox", NAME, "--cancel"], self.home)
        self.assertIn("cancelled 1", p.stdout)
        self.assertEqual(held_files(self.home), [])
        self.target.set_status("idle")
        self.assertTrue(wait_until(self.lock_gone, timeout=5))
        self.assertEqual(self.target.frames, [])

    def test_flush_delivers_even_though_the_target_is_busy(self):
        self.send("push it")
        p = run_cli(["outbox", NAME, "--flush"], self.home, env_extra=FAST)
        self.assertIn("delivered 1 of 1", p.stdout, p.stdout + p.stderr)
        self.assertTrue(wait_until(lambda: self.target.frames, timeout=5))

    def test_list_shows_the_held_count(self):
        self.send("pending")
        out = run_cli(["list"], self.home).stdout
        self.assertIn("1 held", out)

    def test_ended_session_drops_its_messages_and_tells_the_sender(self):
        self.send("hello?", "asker")
        self.target.remove()
        inbox = os.path.join(self.home, ".agent-peer", "inboxes", "asker.jsonl")
        self.assertTrue(wait_until(lambda: os.path.exists(inbox), timeout=8), "the sender was never told")
        with open(inbox, encoding="utf-8") as fh:
            note = json.loads(fh.readline())["content"]
        self.assertIn("was not delivered", note)
        self.assertIn("its session ended", note)
        self.assertEqual(held_files(self.home), [])

    def test_expired_message_is_dropped_with_a_note(self):
        self.send("too late", "waiter", env={"AGENT_PEER_OUTBOX_TTL": "0.5"})
        inbox = os.path.join(self.home, ".agent-peer", "inboxes", "waiter.jsonl")
        self.assertTrue(wait_until(lambda: os.path.exists(inbox), timeout=8))
        with open(inbox, encoding="utf-8") as fh:
            self.assertIn("stayed busy", json.loads(fh.readline())["content"])
        self.assertEqual(self.target.frames, [])

    def test_interrupted_delivery_is_never_resent(self):
        pid = os.getpid()
        inflight = os.path.join(self.home, ".agent-peer", "outbox", str(pid), "inflight")
        os.makedirs(inflight)
        os.makedirs(os.path.join(self.home, ".agent-peer", "outbox", str(pid)), exist_ok=True)
        rec = {"id": "deadbeef", "from": "ghost", "from_cwd": None, "sent_at": 1.0, "enqueued_at": 1.0,
               "priority": "now", "content": "maybe sent", "target": NAME, "pid": pid, "session_id": None}
        with open(os.path.join(inflight, "00000000000000000001-deadbeef.json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        self.send("trigger", "starter")
        self.target.set_status("idle")
        self.assertTrue(wait_until(lambda: self.target.frames, timeout=8))
        self.assertNotIn("maybe sent", "\n".join(self.target.texts()))
        inbox = os.path.join(self.home, ".agent-peer", "inboxes", "ghost.jsonl")
        self.assertTrue(wait_until(lambda: os.path.exists(inbox), timeout=3))

    def test_codex_style_managed_session_is_not_held(self):
        path = os.path.join(self.target.sessions, f"{self.target.pid}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"name": NAME, "status": "busy", "managedByAgentPeer": True, "messagingSocketPath": self.target.sock_path}, fh)
        p = self.send("direct")
        self.assertNotIn("Queued", p.stdout)
        self.assertEqual(held_files(self.home), [])


if __name__ == "__main__":
    unittest.main()
