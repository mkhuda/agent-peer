"""`thread --mention-only` stays silent on banter and wakes, with full context, on a mention."""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import isolated_home, run_cli, spawn_cli, stop_cli, wait_until


class MentionOnlyTest(unittest.TestCase):
    def setUp(self):
        self._cm = isolated_home()
        self.home = self._cm.__enter__()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            stop_cli(p)
        self._cm.__exit__(None, None, None)

    def _post(self, sender, text):
        self.assertEqual(run_cli(["send", "--thread", "t1", "--sender", sender, text], self.home).returncode, 0)

    def _arm(self, *flags):
        proc = spawn_cli(["thread", "t1", "--name", "agy-x", *flags], self.home)
        self.procs.append(proc)
        path = os.path.join(self.home, ".agent-peer", "threads", "t1.presence.json")
        self.assertTrue(wait_until(lambda: os.path.exists(path) and "agy-x" in open(path).read(), timeout=5))
        return proc

    def _cursor(self):
        path = os.path.join(self.home, ".agent-peer", "cursors", "thread.t1.agy-x.json")
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("last_seq", 0)

    def _still_waiting(self, proc, seconds=1.5):
        time.sleep(seconds)
        return proc.poll() is None

    def _woke_with(self, proc, *texts):
        self.assertTrue(wait_until(lambda: proc.poll() is not None, timeout=5), "did not wake")
        out = proc.stdout.read()
        self.assertEqual(proc.returncode, 0, out)
        for text in texts:
            self.assertIn(text, out)
        return out

    def test_banter_does_not_wake_and_leaves_the_cursor_alone(self):
        proc = self._arm("--mention-only")
        self._post("bob", "just chatting")
        self._post("bob", "still chatting")
        self.assertTrue(self._still_waiting(proc))
        self.assertEqual(self._cursor(), 0)

    def test_a_mention_wakes_and_prints_only_the_mention_with_a_count_of_what_was_left_out(self):
        proc = self._arm("--mention-only")
        self._post("bob", "earlier banter")
        self._post("bob", "@agy-x over to you")
        out = self._woke_with(proc, "@agy-x over to you")
        self.assertNotIn("earlier banter", out)
        self.assertIn("(+1 not shown. Read them with: agent-peer logs --thread t1 -n 3)", out)
        self.assertEqual(self._cursor(), 3)  # seq 1 is the room's own "joined" event; the cursor passes the hidden post too

    def test_no_note_when_nothing_was_left_out(self):
        proc = self._arm("--mention-only")  # the room's own "joined" event stays unread but is not counted
        self._post("bob", "@agy-x straight to you")
        out = self._woke_with(proc, "@agy-x straight to you")
        self.assertNotIn("not shown", out)

    def test_context_flag_prints_every_unread_post(self):
        proc = self._arm("--mention-only", "--context")
        self._post("bob", "earlier banter")
        self._post("bob", "@agy-x over to you")
        out = self._woke_with(proc, "earlier banter", "@agy-x over to you")
        self.assertNotIn("not shown", out)

    def test_context_without_mention_only_is_refused(self):
        out = run_cli(["thread", "t1", "--name", "agy-x", "--context"], self.home)
        self.assertEqual(out.returncode, 2)
        self.assertIn("--mention-only", out.stderr)

    def test_all_and_stop_wake_it(self):
        for text in ("@all heads up", "[stop] halt now"):
            proc = self._arm("--mention-only")
            self._post("bob", text)
            self._woke_with(proc, text)
            stop_cli(proc)
            self.procs.remove(proc)

    def test_a_mention_that_arrived_before_arming_is_returned_at_once(self):
        self._post("bob", "chat")
        self._post("bob", "@agy-x you there")
        out = run_cli(["thread", "t1", "--name", "agy-x", "--mention-only"], self.home, timeout=5)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("chat", out.stdout.replace("not shown", ""))
        self.assertIn("@agy-x you there", out.stdout)
        self.assertIn("+1 not shown", out.stdout)

    def test_a_mention_ending_a_sentence_with_a_period_wakes(self):
        proc = self._arm("--mention-only")
        self._post("bob", "tolong cek ya @agy-x.")
        self._woke_with(proc, "tolong cek ya @agy-x.")

    def test_a_mention_of_someone_else_or_inside_code_does_not_wake(self):
        proc = self._arm("--mention-only")
        self._post("bob", "@carol please look")
        self._post("bob", "the literal `@agy-x` token")
        self.assertTrue(self._still_waiting(proc))

    def test_own_post_does_not_wake(self):
        proc = self._arm("--mention-only")
        self._post("agy-x", "@agy-x note to self")
        self.assertTrue(self._still_waiting(proc))

    def test_presence_stays_active_while_waiting(self):
        self._arm("--mention-only")
        path = os.path.join(self.home, ".agent-peer", "threads", "t1.presence.json")
        with open(path, encoding="utf-8") as fh:
            self.assertFalse(json.load(fh)["agy-x"]["left"])

    def test_without_the_flag_any_post_still_wakes(self):
        first = self._arm()
        self._woke_with(first, "joined the thread")  # an unflagged arm returns on any unread post, even the join event
        proc = self._arm()
        self._post("bob", "plain banter")
        self._woke_with(proc, "plain banter")

    def test_combining_with_timeout_is_refused(self):
        out = run_cli(["thread", "t1", "--name", "agy-x", "--mention-only", "--timeout", "3"], self.home)
        self.assertEqual(out.returncode, 2)
        self.assertIn("remove --timeout", out.stderr)

    def test_match_wakes_on_a_post_that_does_not_mention_you(self):
        proc = self._arm("--mention-only", "--match", "pc bebas|landed")
        self._post("bob", "banter")
        self._post("bob", "PC BEBAS sekarang")
        out = self._woke_with(proc, "PC BEBAS sekarang")
        self.assertNotIn("banter", out)
        self.assertIn("(+1 not shown.", out)

    def test_match_still_wakes_on_a_mention_and_ignores_banter(self):
        proc = self._arm("--mention-only", "--match", "landed")
        self._post("bob", "nothing relevant")
        self.assertTrue(self._still_waiting(proc))
        self._post("bob", "@agy-x ping")
        self._woke_with(proc, "@agy-x ping")

    def test_match_never_matches_a_system_line_or_your_own_post(self):
        proc = self._arm("--mention-only", "--match", "joined|note")
        self._post("agy-x", "note to self")
        self.assertTrue(self._still_waiting(proc))

    def test_match_without_mention_only_is_refused(self):
        out = run_cli(["thread", "t1", "--name", "agy-x", "--match", "x"], self.home)
        self.assertEqual(out.returncode, 2)
        self.assertIn("--mention-only", out.stderr)

    def test_an_invalid_regex_is_refused_before_waiting(self):
        out = run_cli(["thread", "t1", "--name", "agy-x", "--mention-only", "--match", "(unclosed"], self.home, timeout=5)
        self.assertEqual(out.returncode, 2)
        self.assertIn("not a valid regular expression", out.stderr)


class SplitTest(unittest.TestCase):
    def test_split_counts_real_posts_not_events_and_spans_the_whole_range(self):
        from agent_peer.thread import split_for_mention_only

        unread = [
            {"seq": 4, "from": "system", "type": "event", "content": "x joined the thread"},
            {"seq": 5, "from": "bob", "content": "banter"},
            {"seq": 6, "from": "bob", "content": "@me please"},
            {"seq": 7, "from": "carol", "content": "more banter"},
            {"seq": 8, "from": "bob", "content": "[stop] now"},
        ]
        shown, hidden, span = split_for_mention_only(unread, "me")
        self.assertEqual([m["seq"] for m in shown], [6, 8])
        self.assertEqual(hidden, 2)
        self.assertEqual(span, 5)

    def test_split_with_a_match_adds_real_posts_whose_text_matches(self):
        import re
        from agent_peer.thread import split_for_mention_only

        unread = [
            {"seq": 4, "from": "system", "type": "event", "content": "landed joined the thread"},
            {"seq": 5, "from": "bob", "content": "banter"},
            {"seq": 6, "from": "bob", "content": "a5 LANDED ok"},
            {"seq": 7, "from": "bob", "content": "@me please"},
        ]
        shown, hidden, span = split_for_mention_only(unread, "me", re.compile("landed", re.I))
        self.assertEqual([m["seq"] for m in shown], [6, 7])
        self.assertEqual((hidden, span), (1, 4))


if __name__ == "__main__":
    unittest.main()
