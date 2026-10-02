"""The session reader merges agent-peer's own registry with Claude's. Nothing writes the
agent-peer directory yet, so entries are fixtures in the formats the writer will produce."""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import REPO_ROOT, isolated_env, isolated_home, run_cli

PROBE = """
import json, sys
from agent_peer import registry
target = sys.argv[1] if len(sys.argv) > 1 else None
out = {"sessions": [{k: s.get(k) for k in ("name", "pid", "alive", "source", "status", "jsonPath", "keyFile", "peerToken", "agentType")}
                    for s in registry.get_active_sessions()]}
if target:
    try:
        s, sock, token = registry.resolve_session(target)
        out["resolved"] = {"pid": s["pid"], "source": s["source"], "token": token}
    except ValueError as e:
        out["error"] = str(e)
print(json.dumps(out))
"""


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


class UnionReaderTest(unittest.TestCase):
    def setUp(self):
        ctx = isolated_home()
        self.home = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.claude = os.path.join(self.home, ".claude", "sessions")
        self.own = os.path.join(self.home, ".agent-peer", "sessions")
        os.makedirs(self.claude)
        os.makedirs(self.own)

    def _legacy(self, name, pid=None, session_id="s-legacy", status="idle", status_at=1000, token="tok-legacy", started=0):
        pid = pid or os.getpid()
        meta = {"name": name, "pid": pid, "sessionId": session_id, "status": status,
                "statusUpdatedAt": status_at, "agentType": "AGY", "startedAt": started}
        with open(os.path.join(self.claude, f"{pid}.json"), "w") as f:
            json.dump(meta, f)
        with open(os.path.join(self.claude, f"{pid}.{'a' * 64}.key"), "w") as f:
            json.dump({"peerToken": token}, f)
        return os.path.join(self.claude, f"{pid}.json")

    def _own(self, stem, name, pid=None, session_id="s-own", status="idle", status_at=1000, token="tok-own", started=0, sid=True):
        pid = pid or os.getpid()
        meta = {"name": name, "pid": pid, "sessionId": session_id, "status": status,
                "statusUpdatedAt": status_at, "agentType": "AGY", "agyConversationId": "conv-1", "startedAt": started}
        if not sid:
            del meta["sessionId"]
        with open(os.path.join(self.own, f"{stem}.json"), "w") as f:
            json.dump(meta, f)
        with open(os.path.join(self.own, f"{stem}.key"), "w") as f:
            json.dump({"peerToken": token}, f)
        return os.path.join(self.own, f"{stem}.json")

    def _probe(self, target=None, **env):
        args = [sys.executable, "-c", PROBE] + ([target] if target else [])
        done = subprocess.run(args, cwd=REPO_ROOT, env=isolated_env(self.home, **env),
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_own_entry_is_found_with_its_pid_taken_from_the_record(self):
        path = self._own("agy.conv-1", "agy-own")
        out = self._probe("agy-own")
        [s] = out["sessions"]
        self.assertEqual((s["name"], s["pid"], s["alive"], s["source"]), ("agy-own", os.getpid(), True, "agent-peer"))
        self.assertEqual((s["jsonPath"], s["peerToken"]), (path, "tok-own"))
        self.assertTrue(s["keyFile"].endswith("agy.conv-1.key"))
        self.assertEqual(out["resolved"]["token"], "tok-own")

    def test_claude_entry_is_unchanged(self):
        path = self._legacy("agy-old")
        [s] = self._probe()["sessions"]
        self.assertEqual((s["name"], s["source"], s["jsonPath"], s["peerToken"]), ("agy-old", "claude", path, "tok-legacy"))
        self.assertIn(f"{os.getpid()}.", os.path.basename(s["keyFile"]))

    def test_a_dead_pid_is_listed_as_not_alive(self):
        self._own("pid.dead", "gone", pid=_dead_pid(), session_id="s-dead")
        [s] = self._probe()["sessions"]
        self.assertFalse(s["alive"])

    def test_one_listener_in_both_directories_is_one_session_and_resolves(self):
        self._own("agy.conv-1", "agy-mirror", session_id="same")
        self._legacy("agy-mirror", session_id="same")
        out = self._probe("agy-mirror")
        self.assertEqual(len(out["sessions"]), 1)
        self.assertEqual(out["sessions"][0]["source"], "agent-peer")
        self.assertNotIn("error", out)
        self.assertEqual(out["resolved"]["token"], "tok-own")

    def test_the_newer_status_wins_between_two_copies(self):
        self._own("agy.conv-1", "agy-mirror", session_id="same", status="idle", status_at=1000)
        self._legacy("agy-mirror", session_id="same", status="new-msg", status_at=2000)
        [s] = self._probe()["sessions"]
        self.assertEqual(s["status"], "new-msg")

    def test_two_different_sessions_are_both_listed(self):
        self._own("agy.conv-1", "agy-one", session_id="one")
        self._legacy("agy-two", pid=_dead_pid(), session_id="two")
        names = sorted(s["name"] for s in self._probe()["sessions"])
        self.assertEqual(names, ["agy-one", "agy-two"])

    def test_an_inherited_legacy_switch_does_not_leak_into_the_tests(self):
        os.environ["AGENT_PEER_REGISTRY"] = "legacy"
        self.addCleanup(os.environ.pop, "AGENT_PEER_REGISTRY", None)
        self._own("agy.conv-1", "agy-own")
        self.assertEqual([s["name"] for s in self._probe()["sessions"]], ["agy-own"])

    def test_legacy_switch_ignores_the_new_directory(self):
        self._own("agy.conv-1", "agy-own")
        self._legacy("agy-old", pid=_dead_pid(), session_id="old")
        names = [s["name"] for s in self._probe(AGENT_PEER_REGISTRY="legacy")["sessions"]]
        self.assertEqual(names, ["agy-old"])

    def test_unreadable_entries_are_skipped(self):
        with open(os.path.join(self.own, "broken.json"), "w") as f:
            f.write("{not json")
        with open(os.path.join(self.own, "nopid.json"), "w") as f:
            json.dump({"name": "nopid"}, f)
        with open(os.path.join(self.own, "x.json.tmp.123"), "w") as f:
            json.dump({"name": "tmp", "pid": os.getpid()}, f)
        self.assertEqual(self._probe()["sessions"], [])

    def test_missing_new_directory_is_fine(self):
        os.rmdir(self.own)
        self._legacy("agy-old")
        self.assertEqual([s["name"] for s in self._probe()["sessions"]], ["agy-old"])

    def test_the_same_session_id_on_different_pids_is_not_merged(self):
        self._own("agy.conv-1", "agy-one", session_id="reused")
        self._legacy("agy-two", pid=_dead_pid(), session_id="reused")
        self.assertEqual(sorted(s["name"] for s in self._probe()["sessions"]), ["agy-one", "agy-two"])

    def test_entries_without_a_session_id_are_never_merged_by_it(self):
        self._own("agy.conv-1", "agy-one", sid=False)
        self._legacy("agy-two", pid=_dead_pid(), session_id="")
        self.assertEqual(sorted(s["name"] for s in self._probe()["sessions"]), ["agy-one", "agy-two"])

    def test_a_reused_pid_keeps_only_the_newer_listener_and_resolves(self):
        self._legacy("agy-stale", session_id="stale", started=1000, token="tok-stale")
        self._own("agy.conv-1", "agy-fresh", session_id="fresh", started=2000)
        out = self._probe(str(os.getpid()))
        self.assertEqual([s["name"] for s in out["sessions"]], ["agy-fresh"])
        self.assertEqual((out["resolved"]["source"], out["resolved"]["token"]), ("agent-peer", "tok-own"))

    def test_a_tie_on_a_reused_pid_goes_to_the_agent_peer_entry(self):
        self._legacy("agy-same", session_id="a", started=1000)
        self._own("agy.conv-1", "agy-same", session_id="b", started=1000)
        out = self._probe("agy-same")
        self.assertEqual(len(out["sessions"]), 1)
        self.assertNotIn("error", out)
        self.assertEqual(out["resolved"]["source"], "agent-peer")

    def test_an_older_copy_does_not_override_a_newer_status(self):
        self._own("agy.conv-1", "agy-mirror", session_id="same", status="new-msg", status_at=3000)
        self._legacy("agy-mirror", session_id="same", status="idle", status_at=1000)
        [s] = self._probe()["sessions"]
        self.assertEqual(s["status"], "new-msg")

    def test_the_claude_filename_pid_wins_over_the_pid_in_the_record(self):
        path = self._legacy("agy-old", pid=_dead_pid(), session_id="old")
        with open(path) as f:
            meta = json.load(f)
        meta["pid"] = os.getpid()
        with open(path, "w") as f:
            json.dump(meta, f)
        [s] = self._probe()["sessions"]
        self.assertNotEqual(s["pid"], os.getpid())
        self.assertFalse(s["alive"])

    def test_sessions_come_back_sorted_by_name_then_pid(self):
        self._own("b", "zz-b", session_id="b")
        self._legacy("zz-a", pid=_dead_pid(), session_id="a")
        self.assertEqual([s["name"] for s in self._probe()["sessions"]], ["zz-a", "zz-b"])

    def test_a_key_file_that_does_not_parse_is_still_known_and_pruned_with_its_entry(self):
        self._own("pid.dead", "gone", pid=_dead_pid(), session_id="s-dead")
        key = os.path.join(self.own, "pid.dead.key")
        with open(key, "w") as f:
            f.write("{garbage")
        [s] = self._probe()["sessions"]
        self.assertEqual((s["peerToken"], s["keyFile"]), (None, key))
        pruned = run_cli(["prune"], self.home)
        self.assertEqual(pruned.returncode, 0, pruned.stderr)
        self.assertEqual(os.listdir(self.own), [])

    def test_list_shows_sessions_from_both_directories(self):
        self._own("agy.conv-1", "agy-own", session_id="own")
        self._legacy("agy-old", pid=_dead_pid(), session_id="old")
        out = run_cli(["list"], self.home).stdout
        self.assertIn("agy-own", out)
        self.assertIn("agy-old", out)
        self.assertIn("Total: 2 sessions", out)


if __name__ == "__main__":
    unittest.main()
