"""Waiting on a thread reads only what was appended, and costs one stat while the room is quiet."""

import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import isolated_home, run_cli, run_py, spawn_cli, stop_cli, wait_until


def _append(path, *records, raw=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for i, r in enumerate(records):
            f.write(json.dumps(r) + "\n")
        if raw:
            f.write(raw)


class ThreadTailTest(unittest.TestCase):
    def setUp(self):
        ctx = isolated_home()
        self.home = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.path = os.path.join(self.home, ".agent-peer", "threads", "t1.jsonl")

    def _py(self, code):
        done = run_py(self.home, "import json, os\nfrom agent_peer import thread\n" + code)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_a_poll_returns_only_what_was_appended_since_the_last_one(self):
        _append(self.path, {"seq": 1, "from": "a", "content": "one"}, {"seq": 2, "from": "a", "content": "two"})
        out = self._py(
            "tail = thread.ThreadTail('t1')\nfirst = [r['seq'] for r in tail.poll()]\nsecond = tail.poll()\n"
            f"open({self.path!r}, 'a').write(json.dumps({{'seq': 3, 'from': 'b', 'content': 'three'}}) + chr(10))\n"
            "third = [r['seq'] for r in tail.poll()]\nprint(json.dumps([first, second, third]))\n")
        self.assertEqual(out, [[1, 2], [], [3]])

    def test_a_half_written_last_line_waits_for_its_newline(self):
        _append(self.path, {"seq": 1, "from": "a", "content": "one"}, raw='{"seq": 2, "from": "a", "con')
        out = self._py(
            "tail = thread.ThreadTail('t1')\nfirst = [r['seq'] for r in tail.poll()]\n"
            f"open({self.path!r}, 'a').write('tent\": \"two\"}}' + chr(10))\n"
            "second = [r['seq'] for r in tail.poll()]\nprint(json.dumps([first, second]))\n")
        self.assertEqual(out, [[1], [2]])

    def test_corrupt_lines_are_skipped_and_a_missing_file_is_empty(self):
        out = self._py("print(json.dumps(thread.ThreadTail('nope').poll()))\n")
        self.assertEqual(out, [])
        _append(self.path, {"seq": 1, "from": "a", "content": "ok"}, raw="not json\n\n")
        _append(self.path, {"seq": 2, "from": "a", "content": "ok2"})
        out = self._py("print(json.dumps([r['seq'] for r in thread.ThreadTail('t1').poll()]))\n")
        self.assertEqual(out, [1, 2])

    def test_a_torn_last_line_is_not_re_read_until_the_file_grows(self):
        _append(self.path, {"seq": 1, "from": "a", "content": "one"}, raw='{"seq": 2, "from": "a", "con')
        out = self._py(
            "tail = thread.ThreadTail('t1')\nfirst = [r['seq'] for r in tail.poll()]\n"
            "opens = [0]\nreal = open\n"
            "def counting(*a, **k):\n    opens[0] += 1\n    return real(*a, **k)\n"
            "thread.open = counting\n"
            "idle = [tail.poll() for _ in range(50)]\n"
            "thread.open = real\n"
            f"open({self.path!r}, 'a').write('tent\": \"two\"}}' + chr(10))\n"
            "after = [r['seq'] for r in tail.poll()]\n"
            "print(json.dumps({'first': first, 'idle_empty': all(i == [] for i in idle), 'opens': opens[0], 'after': after}))\n")
        self.assertEqual((out["first"], out["idle_empty"], out["opens"], out["after"]), ([1], True, 0, [2]))

    def test_a_torn_tail_after_complete_lines_is_also_read_once(self):
        _append(self.path, {"seq": 1, "from": "a", "content": "one"})
        out = self._py(
            "tail = thread.ThreadTail('t1')\nfirst = [r['seq'] for r in tail.poll()]\n"
            f"open({self.path!r}, 'a').write(json.dumps({{'seq': 2, 'from': 'a', 'content': 'two'}}) + chr(10) + '{{\"seq\": 3')\n"
            "second = [r['seq'] for r in tail.poll()]\n"
            "opens = [0]\nreal = open\n"
            "def counting(*a, **k):\n    opens[0] += 1\n    return real(*a, **k)\n"
            "thread.open = counting\n"
            "idle = [tail.poll() for _ in range(50)]\n"
            "thread.open = real\n"
            f"open({self.path!r}, 'a').write(', \"from\": \"b\", \"content\": \"x\"}}' + chr(10))\n"
            "third = [r['seq'] for r in tail.poll()]\n"
            "print(json.dumps([first, second, opens[0], third]))\n")
        self.assertEqual(out, [[1], [2], 0, [3]])

    def test_a_replaced_shorter_file_is_read_from_the_start_again(self):
        _append(self.path, *[{"seq": i, "from": "a", "content": "x" * 50} for i in range(1, 6)])
        out = self._py(
            "tail = thread.ThreadTail('t1')\ntail.poll()\n"
            f"open({self.path!r}, 'w').write(json.dumps({{'seq': 1, 'from': 'z', 'content': 'new'}}) + chr(10))\n"
            "print(json.dumps([r['from'] for r in tail.poll()]))\n")
        self.assertEqual(out, ["z"])

    def test_a_quiet_wait_parses_the_thread_once_and_a_new_post_wakes_it(self):
        _append(self.path, *[{"seq": i, "from": "a", "content": "old"} for i in range(1, 2001)])
        out = self._py(
            "import threading, time\n"
            "thread._write_thread_cursor('t1', 'me', 2000)\n"
            "calls = [0]\nreal = json.loads\n"
            "def counting(s, *a, **k):\n    calls[0] += 1\n    return real(s, *a, **k)\n"
            "thread.json.loads = counting\n"
            "def post():\n    time.sleep(1.0)\n"
            f"    open({self.path!r}, 'a').write(json.dumps({{'seq': 2001, 'from': 'b', 'content': 'hi'}}) + chr(10))\n"
            "threading.Thread(target=post, daemon=True).start()\n"
            "start = time.time()\n"
            "got = thread.wait_for_thread_message('t1', 'me', timeout=10)\n"
            "print(json.dumps({'seqs': [r['seq'] for r in got][-1:], 'parsed': calls[0], 'took': time.time() - start}))\n")
        self.assertEqual(out["seqs"], [2001])
        self.assertLess(out["parsed"], 2100, "the quiet seconds must not re-parse the thread")
        self.assertLess(out["took"], 3.0)

    def test_logs_follow_prints_the_history_once_then_each_new_post_once(self):
        _append(self.path, *[{"seq": i, "from": "a", "content": f"m{i}"} for i in range(1, 6)])
        proc = spawn_cli(["logs", "--thread", "t1", "-n", "2", "--follow", "--raw"], self.home)
        self.addCleanup(stop_cli, proc)
        time.sleep(1.2)
        _append(self.path, {"seq": 6, "from": "b", "content": "m6"})
        _append(self.path, {"seq": 7, "from": "b", "content": "m7"})
        time.sleep(1.5)
        proc.terminate()
        out, _ = proc.communicate(timeout=5)
        seqs = [json.loads(l)["seq"] for l in out.splitlines() if l.startswith("{")]
        self.assertEqual(seqs, [4, 5, 6, 7])

    def test_a_peek_leaves_the_cursor_and_mention_only_still_works_through_the_tail(self):
        _append(self.path, {"seq": 1, "from": "a", "content": "chatter"})
        proc = spawn_cli(["thread", "t1", "--name", "me", "--mention-only"], self.home)
        self.addCleanup(stop_cli, proc)
        time.sleep(1.0)
        self.assertIsNone(proc.poll(), "no mention yet: it must keep waiting")
        _append(self.path, {"seq": 2, "from": "a", "content": "@me look"})
        self.assertTrue(wait_until(lambda: proc.poll() == 0, timeout=8))
        self.assertIn("@me look", proc.stdout.read())


if __name__ == "__main__":
    unittest.main()
