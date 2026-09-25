"""The foreman-facing interactive `join`/`thread -i` - agents keep using the
plain `agent-peer thread <id>`; this covers the non-TTY-dependent logic
(new-thread detection, cwd-scoped candidates, graceful no-picker fallback)
plus the live loop end-to-end via piped stdin. Subprocess-isolated
throughout (never imports agent_peer directly - its paths bind at import
time and would go stale across tests sharing one process, see helpers.py)."""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import isolated_home, run_cli

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_py(home, script, input=None, timeout=8):
    env = dict(os.environ, HOME=home)
    return subprocess.run(
        [sys.executable, "-c", script],
        input=input, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=timeout,
    )


def _run_join_piped(home, thread_id, participant, lines, timeout=8):
    script = f"""
import sys
sys.path.insert(0, {REPO_ROOT!r})
from agent_peer.join import run_join
run_join({thread_id!r}, {participant!r})
"""
    return _run_py(home, script, input=lines, timeout=timeout)


def test_thread_is_new_before_first_post_and_not_after():
    with isolated_home() as home:
        check = f"""
import sys
sys.path.insert(0, {REPO_ROOT!r})
from agent_peer.join import _thread_is_new
print(_thread_is_new('brandnew'))
"""
        r = _run_py(home, check)
        assert r.stdout.strip() == "True", r.stderr

        run_cli(["send", "--thread", "brandnew", "seed", "--sender", "foreman"], home)

        r = _run_py(home, check)
        assert r.stdout.strip() == "False", r.stderr


def test_candidate_sessions_scoped_to_cwd_by_default():
    with isolated_home() as home:
        sessions_dir = os.path.join(home, ".claude", "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        here = REPO_ROOT
        elsewhere = os.path.join(home, "elsewhere-project")

        # Two genuinely alive processes (real PIDs) - registry.py filters
        # by kill(pid, 0), a fake/dead PID would just be dropped as ALIVE:no.
        procs = [subprocess.Popen(["sleep", "5"]) for _ in range(2)]
        try:
            for proc, name, cwd in zip(procs, ("in-scope", "out-of-scope"), (here, elsewhere)):
                pid = proc.pid
                with open(os.path.join(sessions_dir, f"{pid}.json"), "w", encoding="utf-8") as f:
                    json.dump({"pid": pid, "name": name, "cwd": cwd, "status": "idle"}, f)
                with open(os.path.join(sessions_dir, f"{pid}.{'a' * 64}.key"), "w", encoding="utf-8") as f:
                    json.dump({"peerToken": "t"}, f)

            script = f"""
import os, sys
sys.path.insert(0, {REPO_ROOT!r})
os.chdir({here!r})
from agent_peer.join import _candidate_sessions
print(sorted(s['name'] for s in _candidate_sessions(all_scope=False)))
"""
            r = _run_py(home, script)
            assert r.stdout.strip() == "['in-scope']", (r.stdout, r.stderr)
        finally:
            for proc in procs:
                proc.terminate()
                proc.wait(timeout=5)


def test_mention_candidates_union_thread_participants_and_same_cwd_sessions():
    """@mention Tab-completion pool: this thread's own participants (any
    cwd - they already joined) union same-cwd ALIVE sessions (any thread) -
    not the whole mesh, and never includes the asking participant's own
    name."""
    with isolated_home() as home:
        sessions_dir = os.path.join(home, ".claude", "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        here = REPO_ROOT
        elsewhere = os.path.join(home, "elsewhere-project")

        procs = [subprocess.Popen(["sleep", "5"]) for _ in range(2)]
        try:
            for proc, name, cwd in zip(procs, ("in-scope", "out-of-scope"), (here, elsewhere)):
                pid = proc.pid
                with open(os.path.join(sessions_dir, f"{pid}.json"), "w", encoding="utf-8") as f:
                    json.dump({"pid": pid, "name": name, "cwd": cwd, "status": "idle"}, f)
                with open(os.path.join(sessions_dir, f"{pid}.{'a' * 64}.key"), "w", encoding="utf-8") as f:
                    json.dump({"peerToken": "t"}, f)

            # "out-of-scope" is also a thread participant despite being in a
            # different cwd - must still show up (already joined = relevant).
            run_cli(["send", "--thread", "mtc", "hi", "--sender", "out-of-scope"], home)
            run_cli(["thread", "mtc", "--name", "out-of-scope", "--timeout", "1"], home)

            script = f"""
import os, sys
sys.path.insert(0, {REPO_ROOT!r})
os.chdir({here!r})
from agent_peer.join import _mention_candidates
print(sorted(_mention_candidates("mtc", "me")))
"""
            r = _run_py(home, script)
            assert r.stdout.strip() == "['in-scope', 'out-of-scope']", (r.stdout, r.stderr)
        finally:
            for proc in procs:
                proc.terminate()
                proc.wait(timeout=5)


def test_run_join_prints_backlog_sends_lines_and_exits_on_eof():
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "backlog from other agent", "--sender", "other-agent"], home)

        result = _run_join_piped(home, "sync", "foreman", "hello from foreman\n")
        assert result.returncode == 0, result.stderr
        assert "backlog from other agent" in result.stdout
        assert "Left the thread." in result.stdout

        path = os.path.join(home, ".agent-peer", "threads", "sync.jsonl")
        with open(path, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        joins = [r for r in records if r.get("from") == "system" and r.get("event") == "join"]
        assert len(joins) == 1 and joins[0]["who"] == "foreman"  # 0031: joining emits one event
        assert records[-1]["from"] == "system" and records[-1]["event"] == "leave"
        human = [r for r in records if r.get("from") != "system"]
        assert human[-1]["from"] == "foreman"
        assert human[-1]["content"] == "hello from foreman"


def test_rejoin_shows_the_foremans_own_past_messages_in_backlog():
    """Backlog is full history, not a live feed - self-echo suppression only
    belongs in the poll loop (what you just typed is already visible from
    your own terminal echo); a REJOIN has no such echo to rely on."""
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "my own first message", "--sender", "foreman"], home)
        result = _run_join_piped(home, "sync", "foreman", "\n")
        assert result.returncode == 0, result.stderr
        assert "my own first message" in result.stdout


def test_run_join_prints_the_sent_message_once_not_twice():
    """run_join prints one explicit card for the message you just sent (a
    real bug, caught live during hand-walk: raw mode's own redraw cleared
    the typed line, so it visually vanished on Enter - the fix in join.py
    prints the sent record's card itself, verified end-to-end against a
    real pty in test_rawline.py). The poller must not ALSO print it via
    self-echo, which would duplicate it."""
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "seed", "--sender", "other-agent"], home)
        result = _run_join_piped(home, "sync", "foreman", "a message from me\n")
        assert result.returncode == 0, result.stderr
        assert result.stdout.count("a message from me") == 1
        assert result.stdout.count("#3") == 1  # sent card seq (join event took #2, 0031)
        assert "joined the thread" in result.stdout

        path = os.path.join(home, ".agent-peer", "threads", "sync.jsonl")
        with open(path, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        assert records[-1]["from"] == "system" and records[-1]["event"] == "leave"
        human = [r for r in records if r.get("from") != "system"]
        assert human[-1]["from"] == "foreman" and human[-1]["content"] == "a message from me"


def test_run_join_shows_a_live_message_from_another_agent_while_waiting():
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "seed", "--sender", "other-agent"], home)

        script = f"""
import sys, time, subprocess
sys.path.insert(0, {REPO_ROOT!r})
from agent_peer.join import run_join
run_join('sync', 'foreman')
"""
        env = dict(os.environ, HOME=home)
        proc = subprocess.Popen(
            [sys.executable, "-c", script], cwd=REPO_ROOT, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(0.8)  # let the poller start before the live message lands
        run_cli(["send", "--thread", "sync", "live incoming reply", "--sender", "other-agent"], home)
        time.sleep(0.8)  # give the 0.5s poll tick a chance to pick it up
        out, err = proc.communicate(input="\n", timeout=8)
        assert "live incoming reply" in out, (out, err)


def _write_presence(home, thread_id, presence):
    path = os.path.join(home, ".agent-peer", "threads", f"{thread_id}.presence.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(presence, f)


def _register_session(home, name, cwd, status="idle"):
    """A real sleeping process registered as a candidate session (0032) -
    registry.py filters by kill(pid, 0), a fake PID would just be dropped."""
    sessions_dir = os.path.join(home, ".claude", "sessions")
    os.makedirs(sessions_dir, exist_ok=True)
    proc = subprocess.Popen(["sleep", "5"])
    with open(os.path.join(sessions_dir, f"{proc.pid}.json"), "w", encoding="utf-8") as f:
        json.dump({"pid": proc.pid, "name": name, "cwd": cwd, "status": status}, f)
    with open(os.path.join(sessions_dir, f"{proc.pid}.{'a' * 64}.key"), "w", encoding="utf-8") as f:
        json.dump({"peerToken": "t"}, f)
    return proc


def test_invite_agents_excludes_active_but_not_soft_left_participants():
    """0032 Option (c): a session already ACTIVE in the room is a redundant
    invite target and must be excluded. A soft-left one (e.g. Codex's own
    bounded-peek idiom, which always leaves it `left: true`) is legitimately
    re-invitable and must stay a candidate."""
    with isolated_home() as home:
        active_proc = _register_session(home, "active-agent", REPO_ROOT)
        left_proc = _register_session(home, "left-agent", REPO_ROOT)
        try:
            _write_presence(home, "sync", {
                "active-agent": {"pid": 1, "last_seen": time.time(), "left": False},
                "left-agent": {"pid": 2, "last_seen": time.time(), "left": True},
            })
            script = f"""
import os, sys
sys.path.insert(0, {REPO_ROOT!r})
os.chdir({REPO_ROOT!r})
from agent_peer.join import _candidate_sessions
from agent_peer.thread import read_thread_presence
presence = read_thread_presence('sync')
active = {{n for n, i in presence.items() if not i.get('left')}}
names = sorted(s['name'] for s in _candidate_sessions(all_scope=False) if s['name'] not in active)
print(names)
"""
            r = _run_py(home, script)
            assert r.stdout.strip() == "['left-agent']", (r.stdout, r.stderr)
        finally:
            active_proc.terminate()
            active_proc.wait(timeout=5)
            left_proc.terminate()
            left_proc.wait(timeout=5)


def test_run_join_skips_invite_picker_when_room_has_an_active_participant():
    """0032 Option (b): a rejoin into a room that already has someone
    genuinely active must not force the picker - zero friction."""
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "seed", "--sender", "other-agent"], home)
        _write_presence(home, "sync", {"other-agent": {"pid": 1, "last_seen": time.time(), "left": False}})
        result = _run_join_piped(home, "sync", "foreman", "\n")
        assert result.returncode == 0, result.stderr
        assert "Could not open the picker" not in result.stdout


def test_run_join_triggers_invite_when_room_is_empty():
    """0032 Option (b): rejoining a room with nobody actively in it must
    still offer to invite, even though the thread already has history (the
    old `_thread_is_new` check alone would have missed this case). A
    candidate must exist, or invite_agents() has nothing to offer and
    returns silently before ever reaching the picker."""
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "seed", "--sender", "other-agent"], home)
        proc = _register_session(home, "candidate-agent", REPO_ROOT)
        try:
            result = _run_join_piped(home, "sync", "foreman", "\n")
        finally:
            proc.terminate()
            proc.wait(timeout=5)
        assert result.returncode == 0, result.stderr
        # No real TTY here, so the picker can't actually open - but reaching
        # for it at all proves the trigger fired.
        assert "Could not open the picker" in result.stdout


def test_slash_invite_is_not_posted_as_a_thread_message():
    with isolated_home() as home:
        result = _run_join_piped(home, "sync", "foreman", "/invite\nhello\n")
        assert result.returncode == 0, result.stderr
        assert "hello" in result.stdout

        path = os.path.join(home, ".agent-peer", "threads", "sync.jsonl")
        with open(path, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        assert not any(r.get("content") == "/invite" for r in records)
        human = [r for r in records if r.get("from") != "system"]
        assert human[-1]["content"] == "hello"


def test_poll_loop_survives_a_bad_iteration():
    """0033: one failing poll iteration (e.g. transient ENOSPC on the
    presence lock) must not kill the poller - a later good iteration
    still delivers, and the thread stays alive."""
    with isolated_home() as home:
        script = f"""
import sys, threading, time
sys.path.insert(0, {REPO_ROOT!r})
from unittest import mock
import agent_peer.join as j
msg = {{"seq": 7, "from": "ally", "content": "still here"}}
calls = []
def fake_read(tid):
    calls.append(1)
    if len(calls) == 1:
        raise OSError(28, "No space left on device")
    return [msg]
stop = threading.Event()
box = [0]
with mock.patch.object(j, "read_thread", side_effect=fake_read):
    th = threading.Thread(
        target=j._poll_loop, args=("tfix3", "me", box, stop, False, None, {{}}, [None]), daemon=True
    )
    th.start()
    time.sleep(1.6)
    alive = th.is_alive()
    stop.set()
    th.join(timeout=3)
print("ALIVE" if alive else "DEAD")
print("GOTMSG" if box[0] == 7 else "NOMSG")
"""
        result = _run_py(home, script, timeout=10)
        assert result.returncode == 0, result.stderr
        assert "ALIVE" in result.stdout, "poller died on a transient iteration error"
        assert "GOTMSG" in result.stdout, "poller never recovered after the bad iteration"


def test_invite_message_is_case_insensitive_to_agent_type():
    """Real bug, caught live: registry.py's get_session_agent_type() returns
    "AGY"/"CODEX" uppercase but its own Claude Code fallback returns
    "Claude" title-case - an invite to a real native Claude session fell
    through to the generic branch instead of matching the Claude-specific
    one, since the check was a case-sensitive `in ("CLAUDE", "MUSE")`."""
    from agent_peer.join import _invite_message

    assert _invite_message("t", "Claude") == _invite_message("t", "CLAUDE")
    assert "Join with:" in _invite_message("t", "Claude")
    assert "bounded peek" in _invite_message("t", "codex")
    assert "skill workflow" in _invite_message("t", "agy")
