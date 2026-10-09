import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_peer import telegram_bridge as tb


class FakeState:
    def __init__(self, **data):
        self.data = dict(data)

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value


def _http_error(code, body):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(json.dumps(body).encode()))


class FormatTest(unittest.TestCase):
    def test_content_is_escaped_before_markup_is_added(self):
        out = tb._format_for_telegram({"from": "a", "content": "<script>x</script> & #12 @bob"})
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("<code>#12</code>", out)
        self.assertIn("<code>@bob</code>", out)

    def test_event_is_italic_and_escaped(self):
        self.assertEqual(tb._format_for_telegram({"type": "event", "content": "a <b> joined"}), "<i>· a &lt;b&gt; joined</i>")


class ApiTest(unittest.TestCase):
    def test_http_error_becomes_a_json_reply(self):
        err = _http_error(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 3}})
        with mock.patch.object(tb.urllib.request, "urlopen", side_effect=err):
            self.assertEqual(tb._api("t", "sendMessage", {}, 1)["parameters"]["retry_after"], 3)

    def test_http_error_without_json_body_still_reports_the_code(self):
        err = urllib.error.HTTPError("https://x", 502, "bad", {}, io.BytesIO(b"<html>"))
        with mock.patch.object(tb.urllib.request, "urlopen", side_effect=err):
            reply = tb._api("t", "getUpdates", {}, 1)
        self.assertEqual((reply["ok"], reply["error_code"]), (False, 502))


class SendTest(unittest.TestCase):
    def setUp(self):
        tb._stop.clear()
        self.sleeps = []
        patcher = mock.patch.object(tb.time, "sleep", side_effect=self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rejected_markup_is_resent_unformatted(self):
        calls = []

        def api(token, method, params, timeout):
            calls.append(params)
            return {"ok": False, "error_code": 400} if "parse_mode" in params else {"ok": True}

        with mock.patch.object(tb, "_api", side_effect=api):
            tb._send_to_telegram("t", "1", "<b>hi</b> &amp; you")
        self.assertEqual(calls[1]["text"], "hi & you")
        self.assertNotIn("parse_mode", calls[1])

    def test_flood_control_waits_the_time_telegram_asks(self):
        replies = iter([{"ok": False, "error_code": 429, "parameters": {"retry_after": 7}}, {"ok": True}])
        with mock.patch.object(tb, "_api", side_effect=lambda *a, **k: next(replies)):
            tb._send_to_telegram("t", "1", "hi")
        self.assertEqual(self.sleeps, [7])

    def test_bad_token_stops_the_bridge(self):
        with mock.patch.object(tb, "_api", return_value={"ok": False, "error_code": 401, "description": "Unauthorized"}):
            tb._send_to_telegram("t", "1", "hi")
        self.assertTrue(tb._stop.is_set())

    def test_long_message_is_split_without_cutting_a_tag_or_entity(self):
        sent = []
        long_text = "<blockquote>" + ("line &amp; more\n" * 600) + "</blockquote>"
        with mock.patch.object(tb, "_api", side_effect=lambda t, m, p, timeout: sent.append(p) or {"ok": True}):
            tb._send_to_telegram("t", "1", long_text)
        self.assertGreater(len(sent), 1)
        for params in sent:
            self.assertLessEqual(len(params["text"]), tb.MAX_TELEGRAM_CHARS)
            self.assertNotIn("parse_mode", params)
            self.assertNotIn("<", params["text"])
            self.assertNotIn("&amp;", params["text"])

    def test_split_prefers_line_boundaries(self):
        parts = tb._split("a" * 30 + "\n" + "b" * 30, limit=40)
        self.assertEqual(parts, ["a" * 30, "b" * 30])


class AuthorizationTest(unittest.TestCase):
    def _msg(self, chat, user):
        return {"chat": {"id": chat}, "from": {"id": user}}

    def test_other_chat_is_rejected(self):
        self.assertFalse(tb._is_authorized(self._msg(2, 5), "1", set()))

    def test_no_allow_list_accepts_the_chat(self):
        self.assertTrue(tb._is_authorized(self._msg(1, 5), "1", set()))

    def test_allow_list_rejects_other_members_of_the_chat(self):
        self.assertFalse(tb._is_authorized(self._msg(-100, 9), "-100", {"5"}))
        self.assertTrue(tb._is_authorized(self._msg(-100, 5), "-100", {"5"}))


class ConfigTest(unittest.TestCase):
    def setUp(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("TELEGRAM_")}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_telegram_keys_are_read_and_environ_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.env"
            path.write_text('DATABASE_URL=secret\nTELEGRAM_BOT_KEY="tok"\n# c\nTELEGRAM_CHAT_ID=42\n')
            os.chmod(path, 0o600)
            before = dict(os.environ)
            cfg = tb.load_config(str(path))
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(cfg, {"TELEGRAM_BOT_KEY": "tok", "TELEGRAM_CHAT_ID": "42"})

    def test_real_environment_wins_over_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.env"
            path.write_text("TELEGRAM_CHAT_ID=1\n")
            os.chmod(path, 0o600)
            with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "2"}):
                self.assertEqual(tb.load_config(str(path))["TELEGRAM_CHAT_ID"], "2")

    def test_group_chat_without_allowed_users_refuses_to_start(self):
        cfg = {"TELEGRAM_BOT_KEY": "t", "TELEGRAM_CHAT_ID": "-100123"}
        with mock.patch.object(tb, "load_config", return_value=cfg):
            self.assertEqual(tb.run("room", "telegram-bridge"), 1)


class RelayTest(unittest.TestCase):
    def test_text_starting_with_a_dash_is_passed_after_a_double_dash(self):
        tb._stop.clear()
        update = {"update_id": 1, "message": {"chat": {"id": 1}, "from": {"id": 1}, "text": "-h please"}}

        def api(token, method, params, timeout):
            tb._stop.set()
            return {"ok": True, "result": [update]}

        with mock.patch.object(tb, "_api", side_effect=api), \
                mock.patch.object(tb.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            tb.telegram_to_thread(["agent-peer"], "room", "telegram-bridge", "t", "1", set(), FakeState(offset=0))
        argv = run.call_args[0][0]
        self.assertEqual(argv[-2:], ["--", "-h please"])

    def test_unauthorized_user_is_never_relayed(self):
        tb._stop.clear()
        update = {"update_id": 1, "message": {"chat": {"id": -1}, "from": {"id": 9}, "text": "do it"}}

        def api(token, method, params, timeout):
            tb._stop.set()
            return {"ok": True, "result": [update]}

        with mock.patch.object(tb, "_api", side_effect=api), \
                mock.patch.object(tb.subprocess, "run") as run:
            tb.telegram_to_thread(["agent-peer"], "room", "telegram-bridge", "t", "-1", {"5"}, FakeState(offset=0))
        run.assert_not_called()

    def test_bad_token_ends_the_poll_loop(self):
        tb._stop.clear()
        with mock.patch.object(tb, "_api", return_value={"ok": False, "error_code": 401}):
            tb.telegram_to_thread(["agent-peer"], "room", "telegram-bridge", "t", "1", set(), FakeState(offset=0))
        self.assertTrue(tb._stop.is_set())


class RestartTest(unittest.TestCase):
    def _follow(self, lines, state, now=1000.0):
        tb._stop.clear()
        proc = mock.Mock()

        def stdout():
            yield from (l + "\n" for l in lines)
            tb._stop.set()

        proc.stdout = stdout()
        proc.stderr = iter(())
        proc.wait.return_value = 0
        sent = []

        def popen(*a, **k):
            return proc

        with mock.patch.object(tb.subprocess, "Popen", side_effect=popen) as pop, \
                mock.patch.object(tb, "_send_to_telegram", side_effect=lambda t, c, text: sent.append(text)), \
                mock.patch.object(tb.time, "time", return_value=now), \
                mock.patch.object(tb.time, "sleep"):
            tb.thread_to_telegram(["ap"], "room", "telegram-bridge", "t", "1", state)
        return sent, pop.call_args[0][0]

    def test_first_run_does_not_relay_the_replayed_old_record(self):
        state = FakeState()
        old = json.dumps({"seq": 5, "ts": 900.0, "from": "a", "content": "old"})
        new = json.dumps({"seq": 6, "ts": 1001.0, "from": "a", "content": "new"})
        sent, cmd = self._follow([old, new], state)
        self.assertEqual(len(sent), 1)
        self.assertIn("new", sent[0])
        self.assertEqual(state.get("last_seq"), 6)
        self.assertEqual(cmd[-2:], ["-n", "1"])

    def test_restart_skips_what_was_already_relayed_and_sends_the_gap(self):
        state = FakeState(last_seq=6)
        lines = [json.dumps({"seq": s, "ts": 1.0, "from": "a", "content": f"m{s}"}) for s in (5, 6, 7, 8)]
        sent, cmd = self._follow(lines, state)
        self.assertEqual([("m7" in s, "m8" in s) for s in sent], [(True, False), (False, True)])
        self.assertEqual(state.get("last_seq"), 8)
        self.assertEqual(cmd[-2:], ["-n", str(tb._REPLAY_ON_RESTART)])

    def test_the_follow_child_runs_in_its_own_session_so_ctrl_c_reaches_only_the_bridge(self):
        with mock.patch.object(tb.subprocess, "Popen") as pop:
            pop.return_value.stdout = iter(())
            pop.return_value.stderr = iter(())
            tb._stop.clear()
            with mock.patch.object(tb.time, "sleep", side_effect=lambda s: tb._stop.set()):
                tb.thread_to_telegram(["ap"], "room", "bridge", "t", "1", FakeState(last_seq=1))
        self.assertEqual(pop.call_args.kwargs.get("start_new_session"), os.name == "posix")

    def test_own_relayed_messages_are_not_echoed_but_still_advance_the_cursor(self):
        state = FakeState(last_seq=1)
        mine = json.dumps({"seq": 2, "ts": 1.0, "from": "telegram-bridge", "content": "from phone"})
        sent, _ = self._follow([mine], state)
        self.assertEqual(sent, [])
        self.assertEqual(state.get("last_seq"), 2)


class StaleBacklogTest(unittest.TestCase):
    def _run(self, records, state, cutoff=1000.0, participant="bridge"):
        sent = []
        with mock.patch.object(tb, "read_thread", return_value=records), \
                mock.patch.object(tb, "_send_to_telegram", side_effect=lambda t, c, text: sent.append(text)):
            tb._skip_stale_backlog("room", participant, "t", "1", state, cutoff)
        return sent

    def _rec(self, seq, ts, sender="a", content="x", **extra):
        return {"seq": seq, "ts": ts, "from": sender, "content": content, **extra}

    def test_old_records_are_skipped_with_one_summary_that_points_at_logs(self):
        state = FakeState(last_seq=10)
        records = [self._rec(s, 100.0) for s in (9, 10, 11, 12, 13)] + [self._rec(14, 1500.0)]
        sent = self._run(records, state)
        self.assertEqual(state.get("last_seq"), 13)
        self.assertEqual(len(sent), 1)
        self.assertIn("3 older messages were skipped", sent[0])
        self.assertIn("agent-peer logs --thread room -n 4", sent[0])

    def test_records_inside_the_window_are_left_for_the_normal_relay(self):
        state = FakeState(last_seq=10)
        sent = self._run([self._rec(11, 1200.0), self._rec(12, 1300.0)], state)
        self.assertEqual((sent, state.get("last_seq")), ([], 10))

    def test_nothing_is_said_when_every_old_record_was_the_bridges_own(self):
        state = FakeState(last_seq=10)
        sent = self._run([self._rec(11, 100.0, sender="bridge")], state)
        self.assertEqual((sent, state.get("last_seq")), ([], 11))

    def test_a_restart_relays_no_backlog_by_default(self):
        self.assertEqual(tb.DEFAULT_MAX_AGE_MINUTES, 0)

    def test_the_first_run_has_no_backlog_to_skip(self):
        state = FakeState()
        self.assertEqual(self._run([self._rec(1, 100.0)], state), [])
        self.assertIsNone(state.get("last_seq"))


class OffsetTest(unittest.TestCase):
    def test_offset_is_saved_after_each_update(self):
        tb._stop.clear()
        state = FakeState(offset=10)
        updates = [{"update_id": 10, "message": {}}, {"update_id": 11, "message": {}}]

        def api(token, method, params, timeout):
            tb._stop.set()
            self.assertEqual(params["offset"], 10)
            return {"ok": True, "result": updates}

        with mock.patch.object(tb, "_api", side_effect=api):
            tb.telegram_to_thread(["ap"], "room", "b", "t", "1", set(), state)
        self.assertEqual(state.get("offset"), 12)

    def test_first_run_skips_the_queued_backlog(self):
        tb._stop.clear()
        state = FakeState()
        calls = []

        def api(token, method, params, timeout):
            calls.append(params)
            if params.get("offset") == -1:
                return {"ok": True, "result": [{"update_id": 40}]}
            tb._stop.set()
            return {"ok": True, "result": []}

        with mock.patch.object(tb, "_api", side_effect=api):
            tb.telegram_to_thread(["ap"], "room", "b", "t", "1", set(), state)
        self.assertEqual(calls[1]["offset"], 41)


class WorkerFailureTest(unittest.TestCase):
    def test_a_crashed_worker_makes_the_bridge_exit_nonzero(self):
        cfg = {"TELEGRAM_BOT_KEY": "t", "TELEGRAM_CHAT_ID": "1"}
        with mock.patch.object(tb, "load_config", return_value=cfg), \
                mock.patch.object(tb, "_State", return_value=FakeState()), \
                mock.patch.object(tb, "thread_to_telegram", side_effect=RuntimeError("boom")), \
                mock.patch.object(tb, "telegram_to_thread"):
            self.assertEqual(tb.run("room", "telegram-bridge"), 1)


if __name__ == "__main__":
    unittest.main()
