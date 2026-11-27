"""Shared test helpers. Tests drive the real CLI as a subprocess with an
isolated $HOME rather than importing agent_peer, since its paths are bound at import time and would go stale across tests sharing one process."""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from contextlib import contextmanager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IS_WINDOWS = sys.platform == "win32"
sys.path.insert(0, REPO_ROOT)

from agent_peer import compat  # noqa: E402


def skip_on_windows(reason):
    """For a test that depends on POSIX-only behaviour (file modes, SIGTERM cleanup, sh scripts, unix socket files)."""
    return unittest.skipIf(IS_WINDOWS, reason)


def skip_tests_on_windows(cls, names, reason):
    """Skip the named tests of `cls` on Windows only."""
    if IS_WINDOWS:
        for name in names:
            setattr(cls, name, unittest.skip(reason)(getattr(cls, name)))


# Short path: a unix socket path is limited to ~104 bytes on macOS.
_SOCKET_ROOT = "/tmp" if os.path.isdir("/tmp") else None
_SOCKET_DIRS = {}


@contextmanager
def isolated_home():
    """A fresh, empty $HOME plus its own socket directory for the duration of the `with` block."""
    # Windows cannot delete a file a just-stopped child still holds open for an instant.
    with tempfile.TemporaryDirectory(prefix="agent-peer-test-", ignore_cleanup_errors=IS_WINDOWS) as home, \
            tempfile.TemporaryDirectory(prefix="ap-", dir=_SOCKET_ROOT, ignore_cleanup_errors=IS_WINDOWS) as socks:
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
    if IS_WINDOWS:
        env["PYTHONUTF8"] = "1"  # a legacy console code page would turn the status symbols into errors
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
        text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
        input=input,
    )


def run_py(home, code, timeout=30):
    """Run a snippet of Python against this tree with the same isolation as run_cli."""
    return subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=isolated_env(home),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def spawn_cli(args, home, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env_extra=None):
    """Start the CLI in the background; caller must stop_cli() it."""
    env = isolated_env(home, **(env_extra or {}))
    return subprocess.Popen(
        [sys.executable, "-m", "agent_peer"] + args,
        cwd=REPO_ROOT,
        env=env,
        stdout=stdout,
        stderr=stderr,
        text=True, encoding="utf-8", errors="replace",
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


class RecordingEndpoint:
    """A fake native-Claude endpoint (unix socket, or a named pipe on Windows) that records the raw bytes of each connection.
    `address` is what a session registry entry advertises as `messagingSocketPath`."""

    def __init__(self, sock_path):
        self.received = []
        self._stop = threading.Event()
        if compat.IS_WINDOWS:
            self.address = f"agent-peer-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
            self._listener = compat.Listener(self.address)
            self._listener.bind()
            self._listener.listen()
        else:
            self.address = sock_path
            if os.path.exists(sock_path):
                os.unlink(sock_path)
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.bind(sock_path)
            self._sock.listen(16)
            self._sock.settimeout(0.2)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _accept(self):
        return self._listener.accept() if compat.IS_WINDOWS else self._sock.accept()[0]

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn = self._accept()
            except socket.timeout:
                continue
            except Exception:
                return
            conn.settimeout(2)
            chunks = []
            try:
                while True:
                    data = conn.recv(65536)
                    if not data:
                        break
                    chunks.append(data)
            except (socket.timeout, OSError):
                pass
            finally:
                conn.close()
            self.received.append(b"".join(chunks))

    def close(self):
        self._stop.set()
        if compat.IS_WINDOWS:
            try:
                compat.connect(self.address, 0.5).close()  # wake the blocked accept
            except OSError:
                pass
            self._listener.close()
            self._thread.join(timeout=3)
        else:
            self._thread.join(timeout=3)
            self._sock.close()

    def text(self):
        return b"".join(self.received).decode("utf-8", errors="replace")


def connect_endpoint(address, timeout=3):
    """A client connection to a session endpoint, whichever transport the platform uses."""
    if compat.IS_WINDOWS:
        return compat.connect(address, timeout)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    client.connect(address)
    return client
