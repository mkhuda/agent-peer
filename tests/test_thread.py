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
        r = run_cli(["send", "--thread", "sync", "sync ready test", "--sender", "foreman"], home)
        assert r.returncode == 0, r.stderr
        assert "seq 1" in r.stdout

        r = run_cli(["thread", "sync", "--name", "eng", "--timeout", "1"], home)
        assert r.returncode == 0, r.stderr
        assert "sync ready test" in r.stdout


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


def test_seq_survives_a_torn_last_line():
    """A crash mid-write can leave a truncated/corrupt final line - the next
    append must find the last *valid* record, not reset seq to 1."""
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "one", "--sender", "foreman"], home)
        run_cli(["send", "--thread", "sync", "two", "--sender", "foreman"], home)

        path = os.path.join(home, ".agent-peer", "threads", "sync.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"seq": 3, "ts": 1, "from": "foreman", "content": "trunc')  # no closing brace, no newline

        r = run_cli(["send", "--thread", "sync", "three", "--sender", "foreman"], home)
        assert r.returncode == 0, r.stderr
        assert "seq 3" in r.stdout, f"expected the torn line to be skipped, not counted: {r.stdout}"


def test_presence_update_is_skipped_not_written_unlocked_when_contended():
    """touch_thread_presence() must never write presence.json while it
    couldn't acquire the lock - simulate contention with a live PID (this
    test process's own) already holding the presence lock."""
    with isolated_home() as home:
        threads_dir = os.path.join(home, ".agent-peer", "threads")
        os.makedirs(threads_dir, exist_ok=True)
        contended_lock = os.path.join(threads_dir, "sync.lock.presence")
        with open(contended_lock, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))  # this test process is alive -> lock stays held

        run_cli(["send", "--thread", "sync", "hello", "--sender", "foreman"], home)
        r = run_cli(["thread", "sync", "--name", "eng", "--timeout", "2"], home)
        assert r.returncode == 0, r.stderr  # the message still gets delivered

        presence_path = os.path.join(threads_dir, "sync.presence.json")
        assert not os.path.exists(presence_path), "presence.json must not be written while the lock was contended"


def test_logs_thread_shows_presence_header_and_messages():
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "sync ready test", "--sender", "foreman"], home)
        run_cli(["thread", "sync", "--name", "worker-1", "--timeout", "1"], home)  # touches presence

        r = run_cli(["logs", "--thread", "sync", "--no-color"], home)
        assert r.returncode == 0, r.stderr
        assert "Present: worker-1" in r.stdout
        assert "sync ready test" in r.stdout
        assert "#1" in r.stdout


def test_logs_thread_with_no_presence_yet_omits_the_header_line():
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "hello", "--sender", "foreman"], home)
        r = run_cli(["logs", "--thread", "sync", "--no-color"], home)
        assert r.returncode == 0, r.stderr
        assert "Present:" not in r.stdout  # nobody has ever run 'thread sync' yet


def test_logs_thread_raw_outputs_clean_jsonl():
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "hello", "--sender", "foreman"], home)
        r = run_cli(["logs", "--thread", "sync", "--raw"], home)
        assert r.returncode == 0, r.stderr
        record = json.loads(r.stdout.strip())
        assert record == {"seq": 1, "from": "foreman", "content": "hello", "ts": record["ts"]}


def test_logs_thread_query_filters_by_content():
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "talk about the render range", "--sender", "foreman"], home)
        run_cli(["send", "--thread", "sync", "unrelated topic", "--sender", "foreman"], home)
        r = run_cli(["logs", "--thread", "sync", "-q", "render", "--no-color"], home)
        assert "render range" in r.stdout
        assert "unrelated topic" not in r.stdout


def test_logs_thread_as_viewer_highlights_their_own_messages():
    with isolated_home() as home:
        run_cli(["send", "--thread", "sync", "from foreman", "--sender", "foreman"], home)
        run_cli(["send", "--thread", "sync", "from an agent", "--sender", "worker-1"], home)

        r = run_cli(["logs", "--thread", "sync", "--as", "foreman", "--no-color"], home)
        assert r.returncode == 0, r.stderr
        assert "foreman (you)" in r.stdout
        assert "worker-1 (you)" not in r.stdout
        assert "═" in r.stdout  # the double divider used only for the viewer's own card


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
