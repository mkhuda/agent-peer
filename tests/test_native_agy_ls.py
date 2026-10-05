"""agy native delivery through the Cascade Language Server: automatic, fail-safe, token never leaks."""

import glob
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_peer import native, protocol
from agent_peer.native import agy_ls
from tests.helpers import REPO_ROOT, isolated_env, isolated_home, run_cli, socket_dir, wait_until

CONV = "f5246d08-228f-41c8-be8a-b6449cd36584"
TOKEN = "secret-csrf-token-value"


class FakeLS:
    """Stdlib stand-in for the Language Server: checks the CSRF header, records requests."""

    def __init__(self, model="MODEL_X", fail_send=False, trajectory_delay=0.0, truncate=0, trajectory_status=200,
                 tail=None, tail_min_window=0):
        self.requests = []
        self.truncate = truncate  # how many trajectory replies to cut short, like a dropped connection
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                outer.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                if self.headers.get("x-codeium-csrf-token") != TOKEN:
                    return self._reply(401, {"code": "unauthenticated"})
                if self.path.endswith("/GetAllCascadeTrajectories"):
                    if tail is None:
                        return self._reply(404, {})
                    return self._reply(200, {"trajectorySummaries": {CONV: {"stepCount": 100}}})
                if self.path.endswith("/GetCascadeTrajectorySteps"):
                    if tail is None:
                        return self._reply(404, {})
                    window = 100 - int(body.get("stepOffset", 0))
                    models = tail if window >= tail_min_window else []
                    return self._reply(200, {"steps": [{"metadata": {"generatorModel": m}} for m in models]})
                if self.path.endswith("/GetCascadeTrajectory"):
                    time.sleep(trajectory_delay)
                    if trajectory_status != 200:
                        return self._reply(trajectory_status, {})
                    if outer.truncate > 0:
                        outer.truncate -= 1
                        self.send_response(200)
                        self.send_header("Content-Length", "500")
                        self.end_headers()
                        self.wfile.write(b'{"traj')
                        self.close_connection = True
                        return
                    traj = {"trajectory": {"generatorMetadata": [{"chatModel": {"model": model}}]}} if model else {}
                    return self._reply(200, traj)
                if self.path.endswith("/SendUserCascadeMessage"):
                    return self._reply(500 if fail_send else 200, {})
                self._reply(404, {})

            def _reply(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.addr = "127.0.0.1:%d" % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def sent(self):
        return [r for r in self.requests if r[0].endswith("/SendUserCascadeMessage")]


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def _dump(path, data):
    with open(path, "w") as fh:
        json.dump(data, fh)


def _env(addr, conv=CONV, token=TOKEN):
    return {"ANTIGRAVITY_LS_ADDRESS": addr, "ANTIGRAVITY_CSRF_TOKEN": token, "ANTIGRAVITY_CONVERSATION_ID": conv}


class AgyLsTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(protocol, "AGENT_PEER_DIR", self._tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = {k: v for k, v in os.environ.items() if k != agy_ls.OFF_SWITCH_ENV}
        patch_env = mock.patch.dict(os.environ, env, clear=True)
        patch_env.start()
        self.addCleanup(patch_env.stop)
        self.ls = FakeLS()
        self.addCleanup(self.ls.close)
        self.session = {"agentType": "AGY", "name": "agy-x", "pid": 4242, "status": "idle", agy_ls.REGISTRY_FIELD: CONV}

    def _register(self, **overrides):
        info = agy_ls.listen_info(_env(self.ls.addr))
        self.assertEqual(info, {agy_ls.REGISTRY_FIELD: CONV})
        return info


class ListenInfoTest(AgyLsTestBase):
    def test_writes_a_private_endpoint_file_and_returns_only_non_secret_fields(self):
        info = self._register()
        self.assertNotIn(TOKEN, json.dumps(info))
        path = agy_ls._endpoint_path(CONV)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        data = _load(path)
        self.assertEqual((data["addr"], data["token"], data["conversationId"], data["pid"]),
                         (self.ls.addr, TOKEN, CONV, os.getpid()))

    def test_environment_that_is_not_agys_yields_nothing(self):
        cases = [
            {},
            _env(self.ls.addr, conv="../../etc/passwd"),
            _env(self.ls.addr, token=""),
            _env("evil.example.com:80"),
            _env("127.0.0.1"),
        ]
        for env in cases:
            self.assertEqual(agy_ls.listen_info(env), {}, env)
        self.assertFalse(glob.glob(os.path.join(self._tmp.name, "agy-ls", "*.json")))

    def test_stale_endpoint_files_are_pruned(self):
        os.makedirs(agy_ls._endpoints_dir())
        stale = os.path.join(agy_ls._endpoints_dir(), "11111111-1111-1111-1111-111111111111.json")
        _dump(stale, {"pid": 999999})
        self._register()
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(agy_ls._endpoint_path(CONV)))


class SendTest(AgyLsTestBase):
    def test_delivers_as_a_user_turn_with_the_conversations_own_model(self):
        self._register()
        result = agy_ls.send(self.session, "[from boss]\nhello")
        self.assertEqual(result["transport"], "agy-ls")
        self.assertEqual(result["target"], "agy-ls:" + CONV)
        (path, headers, body), = self.ls.sent()
        self.assertEqual(headers.get("x-codeium-csrf-token"), TOKEN)
        self.assertEqual(body["cascadeId"], CONV)
        self.assertEqual(body["items"], [{"text": "[from boss]\nhello"}])
        self.assertEqual(body["messageOrigin"], "MESSAGE_ORIGIN_SDK_EXECUTABLE")
        self.assertEqual(body["cascadeConfig"]["plannerConfig"]["requestedModel"]["model"], "MODEL_X")
        self.assertNotIn(TOKEN, json.dumps(result))

    def _falls_back(self, session=None):
        self.assertIsNone(agy_ls.send(session or self.session, "hi"))
        self.assertEqual(self.ls.sent(), [])

    def test_the_off_switch_forces_the_socket_path(self):
        self._register()
        with mock.patch.dict(os.environ, {agy_ls.OFF_SWITCH_ENV: "0"}):
            self._falls_back()

    def test_delivers_whatever_the_targets_status(self):
        self._register()
        for status in ("idle", "new-msg", "busy"):
            self.assertIsNotNone(agy_ls.send(dict(self.session, status=status), "hi"), status)

    def test_falls_back_without_a_registered_conversation(self):
        self._register()
        self._falls_back({k: v for k, v in self.session.items() if k != agy_ls.REGISTRY_FIELD})

    def test_falls_back_when_there_is_no_endpoint_file(self):
        self._falls_back()

    def test_falls_back_when_the_listener_that_wrote_the_endpoint_is_gone(self):
        self._register()
        path = agy_ls._endpoint_path(CONV)
        data = _load(path)
        data["pid"] = 999999
        _dump(path, data)
        self._falls_back()

    def test_never_sends_the_token_to_a_non_loopback_address(self):
        self._register()
        path = agy_ls._endpoint_path(CONV)
        data = _load(path)
        data["addr"] = "evil.example.com:80"
        _dump(path, data)
        self._falls_back()

    def test_falls_back_when_the_server_refuses_or_is_down(self):
        self._register()
        self.ls.close()
        self.assertIsNone(agy_ls.send(self.session, "hi"))

    def test_falls_back_when_the_conversation_has_no_model_or_the_send_fails(self):
        for kwargs in ({"model": None}, {"fail_send": True}):
            ls = FakeLS(**kwargs)
            self.addCleanup(ls.close)
            agy_ls.listen_info(_env(ls.addr))
            self.assertIsNone(agy_ls.send(self.session, "hi"), kwargs)

    def test_a_wrong_token_is_rejected_by_the_server_and_falls_back(self):
        agy_ls.listen_info(_env(self.ls.addr, token="not-the-token"))
        self.assertIsNone(agy_ls.send(self.session, "hi"))
        self.assertEqual(self.ls.sent(), [])


class SenderHookTest(AgyLsTestBase):
    def test_delivery_is_logged_globally_but_not_into_the_targets_own_inbox(self):
        from agent_peer import sender

        self._register()
        with mock.patch.object(sender, "append_inbox") as log:
            result = sender._try_native(self.session, "hello", "boss", "/w", "now")
        self.assertTrue(result["success"])
        self.assertEqual(result["target_socket"], "agy-ls:" + CONV)
        (record,), kwargs = log.call_args
        self.assertEqual(kwargs, {})  # no session_name/session_pid: a copy there would make `wait` repeat it
        self.assertEqual(record["raw"], {"transport": "agy-ls"})
        self.assertRegex(record["content"].splitlines()[0], r"^\[from boss · /w · sent \d\d:\d\d:\d\d\]$")
        self.assertIn("not from your user", record["content"])
        (_, _, body), = self.ls.sent()
        self.assertTrue(body["items"][0]["text"].rstrip().endswith("approval for anything that needs it.)"))

    def test_a_failed_audit_line_after_a_delivery_does_not_turn_it_into_a_second_delivery(self):
        from agent_peer import sender

        self._register()
        with mock.patch.object(sender, "append_inbox", side_effect=OSError("disk full")):
            result = sender._try_native(self.session, "hello", "boss", "/w", "now")
        self.assertIsNotNone(result)
        self.assertEqual(len(self.ls.sent()), 1)

    def test_other_harnesses_take_the_generic_path_and_never_load_the_agy_module(self):
        code = (
            "import sys; from agent_peer import sender;"
            "r=[sender._try_native({'agentType': t, 'name': 'x', 'pid': 1, 'status': 'idle'}, 'm', 'a', '/w', 'now')"
            " for t in ('CODEX', 'MUSE', 'PI', 'OPENCODE', 'CLAUDE', None)];"
            "print(r, 'agent_peer.native.agy_ls' in sys.modules)"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "[None, None, None, None, None, None] False", out.stderr)

    def test_an_agy_session_from_an_older_agent_peer_takes_the_generic_path(self):
        from agent_peer import sender

        self._register()
        older = {k: v for k, v in self.session.items() if k != agy_ls.REGISTRY_FIELD}
        with mock.patch.object(sender, "append_inbox") as log:
            self.assertIsNone(sender._try_native(older, "hello", "boss", "/w", "now"))
        log.assert_not_called()


class IdentityTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(protocol, "AGENT_PEER_DIR", tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _name(self, sessions, env):
        from agent_peer import registry

        with mock.patch.object(registry, "get_active_sessions", return_value=sessions):
            return native.session_name_from_env(env)

    def test_a_resumed_conversation_gets_its_earlier_name_back(self):
        sessions = [{"name": "zz-test-agy-resume", agy_ls.REGISTRY_FIELD: CONV, "alive": False, "startedAt": 5}]
        self.assertEqual(self._name(sessions, _env("127.0.0.1:1")), "zz-test-agy-resume")

    def test_prefers_a_live_registration_over_a_dead_one(self):
        sessions = [
            {"name": "old", agy_ls.REGISTRY_FIELD: CONV, "alive": False, "startedAt": 9},
            {"name": "live", agy_ls.REGISTRY_FIELD: CONV, "alive": True, "startedAt": 1},
        ]
        self.assertEqual(self._name(sessions, _env("127.0.0.1:1")), "live")

    def test_unknown_or_absent_conversation_falls_back(self):
        sessions = [{"name": "other", agy_ls.REGISTRY_FIELD: "11111111-1111-1111-1111-111111111111", "alive": True}]
        self.assertIsNone(self._name(sessions, _env("127.0.0.1:1")))
        self.assertIsNone(self._name(sessions, {}))


class RememberedNameTest(AgyLsTestBase):
    def _name(self, sessions, env=None):
        from agent_peer import registry

        with mock.patch.object(registry, "get_active_sessions", return_value=sessions):
            return native.session_name_from_env(env or _env(self.ls.addr))

    def test_name_survives_a_clean_exit_that_unregistered_the_session(self):
        agy_ls.remember_name(_env(self.ls.addr), "zz-test-agy-resume")
        self.assertEqual(agy_ls.remembered_name(_env(self.ls.addr)), "zz-test-agy-resume")
        self.assertEqual(self._name([]), "zz-test-agy-resume")

    def test_a_remembered_name_now_held_by_a_live_session_is_not_taken(self):
        agy_ls.remember_name(_env(self.ls.addr), "zz-test-agy-resume")
        other = [{"name": "zz-test-agy-resume", "alive": True, agy_ls.REGISTRY_FIELD: "11111111-1111-1111-1111-111111111111"}]
        self.assertIsNone(self._name(other))

    def test_a_registration_wins_over_the_remembered_name(self):
        agy_ls.remember_name(_env(self.ls.addr), "old-name")
        sessions = [{"name": "registered", agy_ls.REGISTRY_FIELD: CONV, "alive": True, "startedAt": 1}]
        self.assertEqual(self._name(sessions), "registered")

    def test_nothing_is_remembered_without_a_conversation_or_name(self):
        agy_ls.remember_name({}, "x")
        agy_ls.remember_name(_env(self.ls.addr), "")
        self.assertIsNone(agy_ls.remembered_name(_env(self.ls.addr)))
        self.assertIsNone(self._name([], {}))


class FallbackAndCacheTest(AgyLsTestBase):
    def _log(self):
        path = os.path.join(agy_ls._endpoints_dir(), "last-fallback.log")
        if not os.path.exists(path):
            return ""
        with open(path) as fh:
            return fh.read()

    def _calls(self):
        calls = []
        real = agy_ls._rpc

        def spy(endpoint, method, body, timeout=5.0):
            calls.append((method, timeout))
            return real(endpoint, method, body, timeout)

        return calls, mock.patch.object(agy_ls, "_rpc", spy)

    def test_the_history_read_gets_a_long_timeout_and_the_send_a_short_one(self):
        self._register()
        calls, patch = self._calls()
        with patch:
            agy_ls.send(self.session, "hi")
        self.assertEqual(calls, [("GetAllCascadeTrajectories", agy_ls._TAIL_TIMEOUT), ("GetCascadeTrajectory", 15.0),
                                 ("SendUserCascadeMessage", 5.0)])

    def test_a_missing_endpoint_is_recorded_without_any_secret(self):
        self.assertIsNone(agy_ls.send(self.session, "hi"))
        self.assertIn("stage=endpoint", self._log())

    def test_a_failed_send_and_a_missing_model_are_recorded_with_their_cause(self):
        for kwargs, expected in (({"fail_send": True}, "stage=send reason=HTTPError http=500"), ({"model": None}, "stage=model")):
            ls = FakeLS(**kwargs)
            self.addCleanup(ls.close)
            agy_ls.listen_info(_env(ls.addr))
            agy_ls.send(self.session, "hi")
            self.assertIn(expected, self._log())
        log = self._log()
        self.assertNotIn(TOKEN, log)
        self.assertNotIn("hi\n", log)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(agy_ls._endpoints_dir(), "last-fallback.log")).st_mode), 0o600)

    def test_nothing_is_recorded_when_the_door_is_simply_off_or_unadvertised(self):
        self._register()
        with mock.patch.dict(os.environ, {agy_ls.OFF_SWITCH_ENV: "0"}):
            agy_ls.send(self.session, "hi")
        agy_ls.send({k: v for k, v in self.session.items() if k != agy_ls.REGISTRY_FIELD}, "hi")
        self.assertEqual(self._log(), "")

    def test_the_log_keeps_only_the_latest_lines(self):
        for _ in range(agy_ls._FALLBACK_LOG_LINES + 20):
            agy_ls.note_fallback(CONV, "send")
        self.assertEqual(len(self._log().splitlines()), agy_ls._FALLBACK_LOG_LINES)

    def test_the_model_is_read_once_per_minute_per_ls_address(self):
        self._register()
        agy_ls.send(self.session, "one")
        agy_ls.send(self.session, "two")
        self.assertEqual(sum(1 for r in self.ls.requests if r[0].endswith("/GetCascadeTrajectory")), 1)
        with mock.patch.object(agy_ls.time, "time", return_value=time.time() + agy_ls._MODEL_CACHE_SECONDS + 1):
            agy_ls.send(self.session, "three")
        self.assertEqual(sum(1 for r in self.ls.requests if r[0].endswith("/GetCascadeTrajectory")), 2)
        other = FakeLS(model="MODEL_Y")  # the LS restarted: a new address must not reuse the old model
        self.addCleanup(other.close)
        agy_ls.listen_info(_env(other.addr))
        agy_ls.send(self.session, "four")
        self.assertEqual(other.sent()[0][2]["cascadeConfig"]["plannerConfig"]["requestedModel"]["model"], "MODEL_Y")

    def _trajectory_calls(self, ls=None):
        return sum(1 for r in (ls or self.ls).requests if r[0].endswith("/GetCascadeTrajectory"))

    def test_a_truncated_model_lookup_is_retried_once(self):
        ls = FakeLS(truncate=1)
        self.addCleanup(ls.close)
        agy_ls.listen_info(_env(ls.addr))
        self.assertIsNotNone(agy_ls.send(self.session, "hi"))
        self.assertEqual((self._trajectory_calls(ls), len(ls.sent())), (2, 1))
        self.assertEqual(self._log(), "")

    def _stale_cache(self, ls, age, addr=None):
        _dump(agy_ls._model_cache_path(CONV), {"model": "MODEL_OLD", "addr": addr or ls.addr, "at": time.time() - age})

    def test_a_failing_model_lookup_falls_back_to_a_recent_cached_model(self):
        ls = FakeLS(truncate=99)
        self.addCleanup(ls.close)
        agy_ls.listen_info(_env(ls.addr))
        self._stale_cache(ls, agy_ls._MODEL_CACHE_SECONDS + 100)
        self.assertIsNotNone(agy_ls.send(self.session, "hi"))
        self.assertEqual(ls.sent()[0][2]["cascadeConfig"]["plannerConfig"]["requestedModel"]["model"], "MODEL_OLD")

    def test_a_failing_model_lookup_without_a_usable_cache_uses_the_socket(self):
        for age, addr in ((None, None), (agy_ls._STALE_MODEL_SECONDS + 600, None), (600, "127.0.0.1:1")):
            ls = FakeLS(truncate=99)
            self.addCleanup(ls.close)
            agy_ls.listen_info(_env(ls.addr))
            if os.path.exists(agy_ls._model_cache_path(CONV)):
                os.unlink(agy_ls._model_cache_path(CONV))
            if age is not None:
                self._stale_cache(ls, age, addr)
            self.assertIsNone(agy_ls.send(self.session, "hi"), (age, addr))
            self.assertEqual(len(ls.sent()), 0)
        self.assertIn("stage=model reason=IncompleteRead", self._log())

    def test_only_a_dropped_read_may_use_a_stale_model_and_a_timeout_is_not_retried(self):
        slow = FakeLS(trajectory_delay=1.5)
        broken = FakeLS(trajectory_status=500)
        for ls in (slow, broken):
            self.addCleanup(ls.close)
        for ls in (slow, broken):
            agy_ls.listen_info(_env(ls.addr))
            self._stale_cache(ls, 120)
            with mock.patch.object(agy_ls, "_MODEL_LOOKUP_TIMEOUT", 0.5):
                self.assertIsNone(agy_ls.send(self.session, "hi"))
            self.assertEqual(self._trajectory_calls(ls), 1)
            self.assertEqual(len(ls.sent()), 0)

    def test_a_retry_after_a_slow_failure_stays_inside_the_lookup_deadline(self):
        ls = FakeLS(trajectory_delay=0.6, truncate=99)
        self.addCleanup(ls.close)
        agy_ls.listen_info(_env(ls.addr))
        started = time.monotonic()
        with mock.patch.object(agy_ls, "_MODEL_LOOKUP_TIMEOUT", 1.2):
            self.assertIsNone(agy_ls.send(self.session, "hi"))
        self.assertLess(time.monotonic() - started, 1.9)

    def _calls_to(self, ls, suffix):
        return sum(1 for r in ls.requests if r[0].endswith(suffix))

    def test_the_model_comes_from_the_latest_steps_without_reading_the_whole_history(self):
        ls = FakeLS(model="MODEL_FULL", trajectory_delay=5.0, tail=["MODEL_OLD", "MODEL_NEW"])
        self.addCleanup(ls.close)
        agy_ls.listen_info(_env(ls.addr))
        started = time.monotonic()
        self.assertIsNotNone(agy_ls.send(self.session, "hi"))
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(ls.sent()[0][2]["cascadeConfig"]["plannerConfig"]["requestedModel"]["model"], "MODEL_NEW")
        self.assertEqual(self._calls_to(ls, "/GetCascadeTrajectory"), 0)

    def test_the_recent_steps_window_is_widened_once_before_the_whole_history_is_read(self):
        ls = FakeLS(model="MODEL_FULL", tail=["MODEL_NEW"], tail_min_window=50)
        self.addCleanup(ls.close)
        agy_ls.listen_info(_env(ls.addr))
        self.assertIsNotNone(agy_ls.send(self.session, "hi"))
        self.assertEqual(self._calls_to(ls, "/GetCascadeTrajectorySteps"), 2)
        self.assertEqual(self._calls_to(ls, "/GetCascadeTrajectory"), 0)
        self.assertEqual(ls.sent()[0][2]["cascadeConfig"]["plannerConfig"]["requestedModel"]["model"], "MODEL_NEW")

    def test_without_a_model_in_the_recent_steps_the_whole_history_is_read(self):
        ls = FakeLS(model="MODEL_FULL", tail=[])
        self.addCleanup(ls.close)
        agy_ls.listen_info(_env(ls.addr))
        self.assertIsNotNone(agy_ls.send(self.session, "hi"))
        self.assertEqual(self._calls_to(ls, "/GetCascadeTrajectory"), 1)
        self.assertEqual(ls.sent()[0][2]["cascadeConfig"]["plannerConfig"]["requestedModel"]["model"], "MODEL_FULL")

    def test_the_latest_generator_model_wins_and_is_preferred_over_a_plan_model(self):
        steps = {"steps": [{"metadata": {"generatorModel": "OLD", "planModel": "PLAN_NEWEST"}},
                           {"metadata": {"generatorModel": "NEW"}}]}
        self.assertEqual(agy_ls._find_last(steps, "generatorModel"), "NEW")
        calls = []

        def fake_rpc(endpoint, method, body, timeout=5.0):
            calls.append(method)
            if method == "GetAllCascadeTrajectories":
                return {"trajectorySummaries": {CONV: {"stepCount": 10}}}
            return steps

        with mock.patch.object(agy_ls, "_rpc", fake_rpc):
            self.assertEqual(agy_ls._model_from_recent_steps({"addr": "x"}, CONV), "NEW")
        self.assertEqual(calls, ["GetAllCascadeTrajectories", "GetCascadeTrajectorySteps"])

    def test_a_recent_steps_lookup_that_cannot_answer_falls_back_to_the_whole_history(self):
        bad_summaries = {"trajectorySummaries": {}}, {"trajectorySummaries": {CONV: {"stepCount": "many"}}}, {}
        for summary in bad_summaries:
            def fake_rpc(endpoint, method, body, timeout=5.0, summary=summary):
                if method == "GetAllCascadeTrajectories":
                    return summary
                raise AssertionError("steps must not be asked without a step count")
            with mock.patch.object(agy_ls, "_rpc", fake_rpc):
                self.assertIsNone(agy_ls._model_from_recent_steps({"addr": "x"}, CONV))

        def steps_unsupported(endpoint, method, body, timeout=5.0):
            if method == "GetAllCascadeTrajectories":
                return {"trajectorySummaries": {CONV: {"stepCount": 10}}}
            raise urllib.error.HTTPError("http://x", 404, "no", {}, None)

        with mock.patch.object(agy_ls, "_rpc", steps_unsupported):
            self.assertIsNone(agy_ls._model_from_recent_steps({"addr": "x"}, CONV))

    def test_the_recent_steps_lookup_gives_up_inside_its_overall_deadline(self):
        ls = FakeLS(model="MODEL_FULL", tail=["MODEL_NEW"], trajectory_delay=0.0)
        self.addCleanup(ls.close)
        agy_ls.listen_info(_env(ls.addr))
        with mock.patch.object(agy_ls, "_TAIL_DEADLINE", 0.0):
            started = time.monotonic()
            self.assertIsNone(agy_ls._model_from_recent_steps(agy_ls._read_endpoint(CONV), CONV))
            self.assertLess(time.monotonic() - started, 1.0)

    def test_a_send_in_flight_says_which_step_it_is_in(self):
        ls = FakeLS(trajectory_delay=1.5)
        self.addCleanup(ls.close)
        agy_ls.listen_info(_env(ls.addr))
        done = []
        worker = threading.Thread(target=lambda: done.append(agy_ls.send(self.session, "hi")))
        worker.start()
        time.sleep(0.6)
        self.assertEqual(agy_ls.progress.get(worker.ident), "history")
        worker.join(timeout=20)
        self.assertNotIn(worker.ident, agy_ls.progress)
        self.assertTrue(done and done[0])

    def test_a_configured_proxy_is_never_used_for_the_loopback_server(self):
        self._register()
        refused = {"http_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9", "no_proxy": "", "NO_PROXY": ""}
        with mock.patch.dict(os.environ, refused):
            self.assertIsNotNone(agy_ls.send(self.session, "via a proxy that would refuse"))
        self.assertEqual(len(self.ls.sent()), 1)

    def test_pruning_stale_endpoints_leaves_the_model_cache_alone(self):
        self._register()
        agy_ls.send(self.session, "hi")
        cache = agy_ls._model_cache_path(CONV)
        self.assertTrue(os.path.exists(cache))
        agy_ls.listen_info(_env(self.ls.addr))
        self.assertTrue(os.path.exists(cache))


class _ListenHarness(unittest.TestCase):
    """Real `agent-peer listen` processes that believe they run inside agy (no tests of its own)."""

    def setUp(self):
        self._cm = isolated_home()
        self.home = self._cm.__enter__()
        self.procs = []
        self.ls = FakeLS()
        self.launcher = os.path.join(self.home, "launcher.py")
        with open(self.launcher, "w") as fh:
            fh.write(
                "import os, sys\n"
                "sys.path.insert(0, %r)\n"
                "from agent_peer import cli\n"
                "cli.detect_harness_identity = lambda: ('agy', os.getpid())\n"
                "sys.argv = ['agent-peer', 'listen'] + sys.argv[1:]\n"
                "cli.main()\n" % REPO_ROOT
            )

    def tearDown(self):
        for proc in self.procs:
            proc.kill()
            proc.wait()
        for session in self._sessions():
            link = os.path.join(socket_dir(self.home), f"{session.get('name')}.sock")
            if str(session.get("name", "")).startswith("zz-test-"):
                for leftover in (link, session.get("messagingSocketPath")):
                    if leftover and (os.path.islink(leftover) or os.path.exists(leftover)):
                        os.unlink(leftover)
        self.ls.close()
        self._cm.__exit__(None, None, None)

    def _sessions(self):
        return [_load(p) for p in glob.glob(os.path.join(self.home, ".claude", "sessions", "*.json"))]

    def _listen(self, *args):
        env = isolated_env(self.home, **_env(self.ls.addr))
        before = {s["pid"] for s in self._sessions()}
        proc = subprocess.Popen([sys.executable, self.launcher, *args], cwd=REPO_ROOT, env=env)
        self.procs.append(proc)
        self.assertTrue(wait_until(lambda: {s["pid"] for s in self._sessions()} - before, timeout=8),
                        "listener never registered")
        return next(s for s in self._sessions() if s["pid"] not in before)


class ListenerForwardTest(_ListenHarness):
    """A frame that reaches an agy listener's socket (a Claude `SendMessage`, an older agent-peer)
    is handed to agy as a user turn instead of waiting in the inbox."""

    def _socket_send(self, name, text):
        done = run_cli(["send", name, text], self.home, env_extra={agy_ls.OFF_SWITCH_ENV: "0"})
        self.assertEqual(done.returncode, 0, done.stderr)

    def _status(self, name):
        return next(s["status"] for s in self._sessions() if s["name"] == name)

    def _inbox(self, name):
        path = os.path.join(self.home, ".agent-peer", "inboxes", f"{name}.jsonl")
        if not os.path.exists(path):
            return ""
        with open(path) as fh:
            return fh.read()

    def test_a_direct_message_becomes_a_user_turn_and_leaves_no_unread_mail(self):
        self._listen("--name", "zz-test-fwd")
        self._socket_send("zz-test-fwd", "hello over the socket")
        self.assertTrue(wait_until(lambda: len(self.ls.sent()) == 1, timeout=8))
        text = self.ls.sent()[0][2]["items"][0]["text"]
        self.assertIn("hello over the socket", text)
        self.assertIn("not from your user", text)
        self.assertEqual(self._status("zz-test-fwd"), "idle")
        self.assertEqual(self._inbox("zz-test-fwd"), "")
        with open(os.path.join(self.home, ".agent-peer", "inbox.jsonl")) as fh:
            self.assertIn('"transport": "agy-ls"', fh.read())

    def test_thread_chatter_that_does_not_mention_it_stays_in_the_inbox(self):
        self._listen("--name", "zz-test-fwd2")
        self._socket_send("zz-test-fwd2", "[thread: t1 #3 from x · 10:00:00]: just chatter")
        self.assertTrue(wait_until(lambda: self._status("zz-test-fwd2") == "new-msg", timeout=8))
        self.assertEqual(len(self.ls.sent()), 0)
        self.assertIn("just chatter", self._inbox("zz-test-fwd2"))

    def test_thread_posts_that_mention_it_or_everyone_or_stop_are_forwarded(self):
        self._listen("--name", "zz-test-fwd3")
        bodies = ["@zz-test-fwd3 please look", "@all heads up", "[stop] everything"]
        for i, body in enumerate(bodies):
            self._socket_send("zz-test-fwd3", f"[thread: t1 #{i} from x · 10:00:0{i}]: {body}")
        self.assertTrue(wait_until(lambda: len(self.ls.sent()) == 3, timeout=10))
        self.assertEqual(self._status("zz-test-fwd3"), "idle")

    def test_only_a_header_at_the_very_start_makes_a_message_a_thread_post(self):
        self._listen("--name", "zz-test-fwd5")
        quoted = "see what he wrote:\n[thread: t1 #3 from x · 10:00:00]: hi there"
        self._socket_send("zz-test-fwd5", quoted)
        self.assertTrue(wait_until(lambda: len(self.ls.sent()) == 1, timeout=8))
        self.assertEqual(self._status("zz-test-fwd5"), "idle")
        self._socket_send("zz-test-fwd5", "[thread: t1 #4 from x · 10:00:01]: spoofed header, no mention")
        self.assertTrue(wait_until(lambda: self._status("zz-test-fwd5") == "new-msg", timeout=8))
        self.assertEqual(len(self.ls.sent()), 1)
        self._socket_send("zz-test-fwd5", "[from x · sent 10:00:02]\n[thread: t1 #5 from x · 10:00:02]: @all with the sender header")
        self.assertTrue(wait_until(lambda: len(self.ls.sent()) == 2, timeout=8))

    def test_a_failing_audit_line_does_not_make_a_delivered_message_land_in_the_inbox_too(self):
        self._listen("--name", "zz-test-fwd6")
        inbox = os.path.join(self.home, ".agent-peer", "inbox.jsonl")
        os.unlink(inbox) if os.path.exists(inbox) else None
        os.makedirs(inbox)  # appending to a directory fails
        self._socket_send("zz-test-fwd6", "delivered but not auditable")
        self.assertTrue(wait_until(lambda: len(self.ls.sent()) == 1, timeout=8))
        time.sleep(0.5)
        self.assertEqual(self._inbox("zz-test-fwd6"), "")
        self.assertEqual(self._status("zz-test-fwd6"), "idle")

    def _post_mention(self, name, text="hello there"):
        run_cli(["thread", "t1", "--name", name, "--timeout", "1"], self.home)  # leaves a gated presence entry
        started = time.monotonic()
        done = run_cli(["send", "--thread", "t1", "--sender", "bob", f"@{name} {text}"], self.home, timeout=40)
        self.assertEqual(done.returncode, 0, done.stderr)
        return time.monotonic() - started

    def test_a_thread_mention_is_handed_to_a_current_listener_so_the_poster_never_waits_for_agy(self):
        self.ls.close()
        self.ls = FakeLS(trajectory_delay=8.0)  # a slow language server: the whole-history read takes 8 s
        self._listen("--name", "zz-test-fwd7")
        self.assertLess(self._post_mention("zz-test-fwd7"), 5.0)
        self.assertTrue(wait_until(lambda: len(self.ls.sent()) == 1, timeout=20))
        self.assertIn("hello there", self.ls.sent()[0][2]["items"][0]["text"])

    def test_at_all_and_stop_posts_take_the_listener_handoff_too(self):
        self._listen("--name", "zz-test-fwd9")
        run_cli(["thread", "t1", "--name", "zz-test-fwd9", "--timeout", "1"], self.home)
        for body in ("@all heads up", "[stop] everything"):
            done = run_cli(["send", "--thread", "t1", "--sender", "bob", body], self.home)
            self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(wait_until(lambda: len(self.ls.sent()) == 2, timeout=15))
        self.assertEqual(self._status("zz-test-fwd9"), "idle")

    def test_a_dead_listener_socket_makes_the_poster_reach_agy_directly(self):
        self._listen("--name", "zz-test-fwd10")
        sock = next(s["messagingSocketPath"] for s in self._sessions() if s["name"] == "zz-test-fwd10")
        os.unlink(sock)
        open(sock, "w").close()  # still listed as reachable, but nothing listens on it
        self._post_mention("zz-test-fwd10")
        self.assertTrue(wait_until(lambda: len(self.ls.sent()) == 1, timeout=15))

    def test_a_listener_that_does_not_forward_is_still_reached_directly(self):
        self._listen("--name", "zz-test-fwd8")
        for path in glob.glob(os.path.join(self.home, ".agent-peer", "sessions", "*.json")) + \
                glob.glob(os.path.join(self.home, ".claude", "sessions", "*.json")):
            meta = _load(path)
            meta.pop("registryVersion", None)
            _dump(path, meta)
        self._post_mention("zz-test-fwd8")
        self.assertTrue(wait_until(lambda: len(self.ls.sent()) == 1, timeout=10))

    def test_a_failed_native_hand_off_keeps_the_message_in_the_inbox(self):
        self._listen("--name", "zz-test-fwd4")
        for path in glob.glob(os.path.join(self.home, ".agent-peer", "agy-ls", "*.json")):
            os.unlink(path)
        self._socket_send("zz-test-fwd4", "nobody can inject this")
        self.assertTrue(wait_until(lambda: self._status("zz-test-fwd4") == "new-msg", timeout=8))
        self.assertEqual(len(self.ls.sent()), 0)
        self.assertIn("nobody can inject this", self._inbox("zz-test-fwd4"))


class ListenEndToEndTest(_ListenHarness):
    """`agent-peer listen` in an agy-like process registers the conversation."""

    def test_registers_the_conversation_without_putting_the_token_in_the_registry(self):
        session = self._listen("--name", "zz-test-agy-e2e")
        self.assertEqual(session["agentType"], "AGY")
        self.assertEqual(session[agy_ls.REGISTRY_FIELD], CONV)
        self.assertNotIn(TOKEN, json.dumps(session))
        endpoint = os.path.join(self.home, ".agent-peer", "agy-ls", CONV + ".json")
        self.assertEqual(stat.S_IMODE(os.stat(endpoint).st_mode), 0o600)
        listing = run_cli(["list"], self.home).stdout
        self.assertIn("zz-test-agy-e2e", listing)
        self.assertNotIn(TOKEN, listing)

    def test_a_clean_exit_then_a_listen_without_a_name_restores_the_name(self):
        first = self._listen("--name", "zz-test-agy-resume")
        self.procs[0].terminate()  # normal stop: the registration is removed
        self.procs[0].wait()
        self.assertFalse([s for s in self._sessions() if s["pid"] == first["pid"]])
        second = self._listen()
        self.assertEqual(second["name"], "zz-test-agy-resume")

    def test_a_resumed_agy_keeps_its_name_without_being_told(self):
        first = self._listen("--name", "zz-test-agy-resume")
        os.kill(first["pid"], 9)  # killed, so its registration stays behind like after a crash
        self.procs[0].wait()
        second = self._listen()
        self.assertEqual(second["name"], "zz-test-agy-resume")


class AgyThreadPushTest(_ListenHarness):
    """Thread knocks reach an agy through its language server - for who deserves a turn, and in time."""

    def _post(self, text, sender="huda"):
        return run_cli(["send", "--thread", "t1", "--sender", sender, text], self.home, timeout=30)

    def _join(self, name, *flags):
        run_cli(["thread", "t1", "--name", name, "--timeout", "1", *flags], self.home)

    def _sent(self, expected, timeout=8):
        return wait_until(lambda: len(self.ls.sent()) >= expected, timeout=timeout)

    def test_a_mention_reaches_a_gated_agy_natively(self):
        self._listen("--name", "zz-test-agy-t")
        self._join("zz-test-agy-t")
        self._post("@zz-test-agy-t please look")
        self.assertTrue(self._sent(1))
        self.assertIn("@zz-test-agy-t please look", self.ls.sent()[0][2]["items"][0]["text"])

    def test_ordinary_posts_to_a_follow_all_agy_from_another_agent_go_by_socket_not_as_a_turn(self):
        self._listen("--name", "zz-test-agy-t")
        self._listen("--name", "zz-test-bob")
        self._join("zz-test-agy-t", "--follow")
        self._post("just chatting", sender="zz-test-bob")
        time.sleep(2)
        self.assertEqual(self.ls.sent(), [])

    def test_a_human_post_to_a_follow_all_agy_does_earn_a_turn(self):
        self._listen("--name", "zz-test-agy-t")
        self._join("zz-test-agy-t", "--follow")
        self._post("hello from a person")
        self.assertTrue(self._sent(1))

    def test_a_mention_from_another_agent_to_a_follow_all_agy_does_earn_a_turn(self):
        self._listen("--name", "zz-test-agy-t")
        self._listen("--name", "zz-test-bob")
        self._join("zz-test-agy-t", "--follow")
        self._post("@zz-test-agy-t over to you", sender="zz-test-bob")
        self.assertTrue(self._sent(1))

    def test_a_slow_history_read_does_not_make_the_short_lived_poster_drop_the_push(self):
        self.ls.close()
        self.ls = FakeLS(trajectory_delay=4.0)  # longer than the old 3 s budget
        self._listen("--name", "zz-test-agy-t")
        self._join("zz-test-agy-t")
        self._post("@zz-test-agy-t please look")
        self.assertTrue(self._sent(1), "the push never arrived")


if __name__ == "__main__":
    unittest.main()
