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
        assert records[-1]["from"] == "foreman"
        assert records[-1]["content"] == "hello from foreman"


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
        assert records[-1]["from"] == "foreman" and records[-1]["content"] == "a message from me"


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
