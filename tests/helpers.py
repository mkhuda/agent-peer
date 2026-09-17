"""Shared test helpers. Tests drive the real CLI as a subprocess with an
isolated $HOME rather than importing agent_peer, since its paths are bound at import time and would go stale across tests sharing one process."""

import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@contextmanager
def isolated_home():
    """A fresh, empty $HOME for the duration of the `with` block."""
    with tempfile.TemporaryDirectory(prefix="agent-peer-test-") as home:
        yield home


def run_cli(args, home, timeout=10, input=None):
    """Run `python -m agent_peer <args>` with the given $HOME, wait for it to
    exit, and return the finished subprocess.CompletedProcess."""
    env = dict(os.environ, HOME=home)
    return subprocess.run(
        [sys.executable, "-m", "agent_peer"] + args,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input,
    )


def spawn_cli(args, home, stdout=subprocess.PIPE, stderr=subprocess.PIPE):
    """Start the CLI in the background; caller must stop_cli() it."""
    env = dict(os.environ, HOME=home)
    return subprocess.Popen(
        [sys.executable, "-m", "agent_peer"] + args,
        cwd=REPO_ROOT,
        env=env,
        stdout=stdout,
        stderr=stderr,
        text=True,
    )


def stop_cli(proc, timeout=5):
    """Terminate a process started with spawn_cli() and close its pipes."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
    for stream in (proc.stdout, proc.stderr, proc.stdin):
        if stream is not None:
            stream.close()


def wait_until(predicate, timeout=5.0, interval=0.05):
    """Poll `predicate()` until it returns truthy or `timeout` elapses.
    Returns the truthy value, or None on timeout."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return None
