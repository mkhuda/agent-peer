"""Shared-thread discussion (send --thread / thread <id>) - a meeting
several sessions can post into freely instead of one-recipient-at-a-time
send. Locks in the fixes found during review: seq-based cursor (not
timestamp), self-echo filtering, and a new participant's cursor starting
at 0 (full backlog) rather than "now"."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import isolated_home, run_cli


def test_thread_backlog_delivered_to_a_late_joiner():
    with isolated_home() as home:
        r = run_cli(["send", "--thread", "sync", "ayo review phase 6", "--sender", "foreman"], home)
        assert r.returncode == 0, r.stderr
        assert "seq 1" in r.stdout

        r = run_cli(["thread", "sync", "--name", "eng", "--timeout", "1"], home)
        assert r.returncode == 0, r.stderr
        assert "ayo review phase 6" in r.stdout


def test_thread_self_echo_is_never_delivered_back_to_its_own_author():
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "hello everyone", "--sender", "foreman"], home)
        r = run_cli(["thread", "sync", "--name", "foreman", "--timeout", "1"], home)
        assert r.returncode == 1
        assert "Timeout" in r.stdout


def test_thread_wait_times_out_cleanly_with_no_backlog():
    with isolated_home() as home:
        r = run_cli(["thread", "empty-thread", "--name", "someone", "--timeout", "0.5"], home)
        assert r.returncode == 1
        assert "Timeout" in r.stdout


def test_thread_second_wait_only_sees_messages_posted_after_the_first():
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "first", "--sender", "foreman"], home)
        r1 = run_cli(["thread", "sync", "--name", "eng", "--timeout", "1"], home)
        assert "first" in r1.stdout

        r2 = run_cli(["thread", "sync", "--name", "eng", "--timeout", "0.3"], home)
        assert r2.returncode == 1  # nothing new yet

        run_cli(["send", "--thread", "sync", "second", "--sender", "foreman"], home)
        r3 = run_cli(["thread", "sync", "--name", "eng", "--timeout", "1"], home)
        assert "second" in r3.stdout
        assert "first" not in r3.stdout  # already consumed, not replayed


def test_send_thread_and_target_together_is_refused():
    with isolated_home() as home:
        r = run_cli(["send", "--thread", "meet1", "somebody", "hi"], home)
        assert r.returncode != 0
        assert "--thread" in r.stderr


def test_send_without_target_or_thread_is_refused():
    with isolated_home() as home:
        r = run_cli(["send", "just one arg"], home)
        assert r.returncode != 0
        assert "Missing target" in r.stderr


def test_concurrent_thread_appends_get_unique_sequential_seq():
    import subprocess
    import sys as _sys

    with isolated_home() as home:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, HOME=home)
        procs = [
            subprocess.Popen(
                [_sys.executable, "-m", "agent_peer", "send", "--thread", "stress", f"msg {i}", "--sender", f"w{i % 3}"],
                cwd=repo_root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for i in range(20)
        ]
        for p in procs:
            assert p.wait(timeout=10) == 0

        path = os.path.join(home, ".agent-peer", "threads", "stress.jsonl")
        with open(path, encoding="utf-8") as f:
            seqs = sorted(json.loads(line)["seq"] for line in f if line.strip())
        assert seqs == list(range(1, 21)), f"expected 1..20 with no gaps/duplicates, got {seqs}"
