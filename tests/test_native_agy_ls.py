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
from tests.helpers import REPO_ROOT, isolated_home, run_cli, wait_until

CONV = "f5246d08-228f-41c8-be8a-b6449cd36584"
TOKEN = "secret-csrf-token-value"


class FakeLS:
    """Stdlib stand-in for the Language Server: checks the CSRF header, records requests."""

    def __init__(self, model="MODEL_X", fail_send=False):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                outer.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                if self.headers.get("x-codeium-csrf-token") != TOKEN:
                    return self._reply(401, {"code": "unauthenticated"})
                if self.path.endswith("/GetCascadeTrajectory"):
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
        sessions = [{"name": "agent-peer-agy", agy_ls.REGISTRY_FIELD: CONV, "alive": False, "startedAt": 5}]
        self.assertEqual(self._name(sessions, _env("127.0.0.1:1")), "agent-peer-agy")

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
        agy_ls.remember_name(_env(self.ls.addr), "agent-peer-agy")
        self.assertEqual(agy_ls.remembered_name(_env(self.ls.addr)), "agent-peer-agy")
        self.assertEqual(self._name([]), "agent-peer-agy")

    def test_a_remembered_name_now_held_by_a_live_session_is_not_taken(self):
        agy_ls.remember_name(_env(self.ls.addr), "agent-peer-agy")
        other = [{"name": "agent-peer-agy", "alive": True, agy_ls.REGISTRY_FIELD: "11111111-1111-1111-1111-111111111111"}]
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


class ListenEndToEndTest(unittest.TestCase):
    """`agent-peer listen` in an agy-like process registers the conversation."""

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
        self.ls.close()
        self._cm.__exit__(None, None, None)

    def _sessions(self):
        return [_load(p) for p in glob.glob(os.path.join(self.home, ".claude", "sessions", "*.json"))]

    def _listen(self, *args):
        env = dict(os.environ, HOME=self.home, **_env(self.ls.addr))
        before = {s["pid"] for s in self._sessions()}
        proc = subprocess.Popen([sys.executable, self.launcher, *args], cwd=REPO_ROOT, env=env)
        self.procs.append(proc)
        self.assertTrue(wait_until(lambda: {s["pid"] for s in self._sessions()} - before, timeout=8),
                        "listener never registered")
        return next(s for s in self._sessions() if s["pid"] not in before)

    def test_registers_the_conversation_without_putting_the_token_in_the_registry(self):
        session = self._listen("--name", "agy-e2e")
        self.assertEqual(session["agentType"], "AGY")
        self.assertEqual(session[agy_ls.REGISTRY_FIELD], CONV)
        self.assertNotIn(TOKEN, json.dumps(session))
        endpoint = os.path.join(self.home, ".agent-peer", "agy-ls", CONV + ".json")
        self.assertEqual(stat.S_IMODE(os.stat(endpoint).st_mode), 0o600)
        listing = run_cli(["list"], self.home).stdout
        self.assertIn("agy-e2e", listing)
        self.assertNotIn(TOKEN, listing)

    def test_a_clean_exit_then_a_listen_without_a_name_restores_the_name(self):
        first = self._listen("--name", "agent-peer-agy")
        self.procs[0].terminate()  # normal stop: the registration is removed
        self.procs[0].wait()
        self.assertFalse([s for s in self._sessions() if s["pid"] == first["pid"]])
        second = self._listen()
        self.assertEqual(second["name"], "agent-peer-agy")

    def test_a_resumed_agy_keeps_its_name_without_being_told(self):
        first = self._listen("--name", "agent-peer-agy")
        os.kill(first["pid"], 9)  # killed, so its registration stays behind like after a crash
        self.procs[0].wait()
        second = self._listen()
        self.assertEqual(second["name"], "agent-peer-agy")


if __name__ == "__main__":
    unittest.main()
