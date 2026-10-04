"""Shared test helpers. Tests drive the real CLI as a subprocess with an
isolated $HOME rather than importing agent_peer, since its paths are bound at import time and would go stale across tests sharing one process."""

import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Short path: a unix socket path is limited to ~104 bytes on macOS.
_SOCKET_ROOT = "/tmp" if os.path.isdir("/tmp") else None
_SOCKET_DIRS = {}


@contextmanager
def isolated_home():
    """A fresh, empty $HOME plus its own socket directory for the duration of the `with` block."""
    with tempfile.TemporaryDirectory(prefix="agent-peer-test-") as home, \
            tempfile.TemporaryDirectory(prefix="ap-", dir=_SOCKET_ROOT) as socks:
        _SOCKET_DIRS[home] = socks
        try:
            yield home
        finally:
            _SOCKET_DIRS.pop(home, None)


_IDENTITY_VARS = ("CLAUDE_CONFIG_DIR", "CODEX_THREAD_ID", "HERDR_ENV", "HERDR_PANE_ID")


def isolated_env(home, **extra):
    """Environment that keeps a child process inside `home` and its socket directory, with
    the caller's own harness identity removed; `extra` opts specific variables back in."""
    env = dict(os.environ, HOME=home, USERPROFILE=home)
    for key in list(env):
        if key in _IDENTITY_VARS or key.startswith(("ANTIGRAVITY_", "AGENT_PEER_")):
            del env[key]
    env["AGENT_PEER_CLAUDE_MIRROR"] = "1"  # most tests look for a listener in Claude's directory
    env.update(extra)
    if home in _SOCKET_DIRS:
        env["AGENT_PEER_SOCKET_DIR"] = _SOCKET_DIRS[home]
    return env


def socket_dir(home):
    return _SOCKET_DIRS[home]


def run_cli(args, home, timeout=10, input=None, env_extra=None):
    """Run `python -m agent_peer <args>` with the given $HOME, wait for it to
    exit, and return the finished subprocess.CompletedProcess."""
    env = isolated_env(home, **(env_extra or {}))
    return subprocess.run(
        [sys.executable, "-m", "agent_peer"] + args,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input,
    )


def run_py(home, code, timeout=30):
    """Run a snippet of Python against this tree with the same isolation as run_cli."""
    return subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=isolated_env(home),
                          capture_output=True, text=True, timeout=timeout)


def spawn_cli(args, home, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env_extra=None):
    """Start the CLI in the background; caller must stop_cli() it."""
    env = isolated_env(home, **(env_extra or {}))
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
