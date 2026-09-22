"""Every OS-specific call agent_peer makes, isolated behind one platform-
neutral interface. Nothing outside this file checks sys.platform. POSIX
implementations are the project's existing, battle-tested behavior moved
here unchanged - Windows implementations are new (see docs/tasks/0023 for
the live-verified research behind each one)."""

import os
import sys
import socket
import subprocess

IS_WINDOWS = sys.platform == "win32"


# --- Transport: bind/accept/connect over the mesh's own two-frame protocol ---
# POSIX: socket.AF_UNIX, unchanged. Windows: Named Pipes via multiprocessing.
# connection (confirmed live, 2026-09-23) - recv_bytes()/send_bytes() already
# preserve message boundaries, so the caller's own line-buffered JSON framing
# (agent_peer/listener.py's handle_client) works unmodified on either backend.

if not IS_WINDOWS:
    class Connection:
        def __init__(self, sock: socket.socket):
            self._sock = sock

        def recv(self, bufsize: int) -> bytes:
            return self._sock.recv(bufsize)

        def sendall(self, data: bytes) -> None:
            self._sock.sendall(data)

        def settimeout(self, seconds) -> None:
            self._sock.settimeout(seconds)

        def close(self) -> None:
            try:
                self._sock.close()
            except OSError:
                pass

    class Listener:
        def __init__(self, address: str):
            self.address = address
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

        def bind(self) -> None:
            self._sock.bind(self.address)

        def listen(self, backlog: int = 10) -> None:
            self._sock.listen(backlog)

        def accept(self) -> Connection:
            client, _ = self._sock.accept()
            return Connection(client)

        def close(self) -> None:
            try:
                self._sock.close()
            except OSError:
                pass

    def connect(address: str, timeout: float = 5.0) -> Connection:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(address)
        return Connection(sock)

else:
    from multiprocessing.connection import Listener as _MPListener, Client as _MPClient

    class Connection:
        def __init__(self, conn):
            self._conn = conn
            self._timeout = None

        def recv(self, bufsize: int) -> bytes:
            # bufsize is a POSIX-socket hint, meaningless here - recv_bytes()
            # already returns exactly one complete message written by one
            # send_bytes() call on the other end.
            if self._timeout is not None:
                if not self._conn.poll(self._timeout):
                    raise TimeoutError("timed out waiting for data")
            try:
                return self._conn.recv_bytes()
            except EOFError:
                return b""

        def sendall(self, data: bytes) -> None:
            self._conn.send_bytes(data)

        def settimeout(self, seconds) -> None:
            self._timeout = seconds

        def close(self) -> None:
            try:
                self._conn.close()
            except OSError:
                pass

    class Listener:
        def __init__(self, address: str):
            # `address` is a pipe name (e.g. "agent-peer-1234"), not a
            # filesystem path - the \\.\pipe\ prefix is added here so every
            # caller can keep passing the same short id used on POSIX.
            self.address = address
            self._pipe_name = r"\\.\pipe\%s" % address
            self._listener = None

        def bind(self) -> None:
            # multiprocessing.connection.Listener binds AND starts listening
            # in one call - nothing to do here except defer construction to
            # listen(), so the bind()/listen() split matches the POSIX side.
            pass

        def listen(self, backlog: int = 10) -> None:
            self._listener = _MPListener(self._pipe_name, family="AF_PIPE")

        def accept(self) -> Connection:
            return Connection(self._listener.accept())

        def close(self) -> None:
            try:
                if self._listener:
                    self._listener.close()
            except OSError:
                pass

    def connect(address: str, timeout: float = 5.0) -> Connection:
        pipe_name = r"\\.\pipe\%s" % address
        conn = _MPClient(pipe_name)
        wrapped = Connection(conn)
        wrapped.settimeout(timeout)
        return wrapped


# --- Process liveness ---
# POSIX: os.kill(pid, 0), unchanged. Windows: OpenProcess + GetExitCodeProcess
# via ctypes against kernel32 (confirmed live, 2026-09-23) - no psutil needed.

if not IS_WINDOWS:
    def is_pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
else:
    import ctypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259

    def is_pid_alive(pid: int) -> bool:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return bool(ok) and exit_code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)


# --- File locking ---
# POSIX previously used fcntl.flock (whole-file advisory lock, held by an open
# fd, auto-released by the kernel if the holder dies - including SIGKILL).
# fcntl doesn't exist on Windows at all. os.O_CREAT|os.O_EXCL is atomic at the
# kernel level on both platforms (confirmed live, 2026-09-23) and needs no
# platform branch - but a plain O_EXCL lock FILE, unlike flock, is not
# auto-released on crash, so acquire_lock writes its own pid into the file and
# treats a lock whose owner is no longer alive (is_pid_alive) as stale and
# reclaims it, restoring the crash-safety flock gave for free.

def acquire_lock(lock_path: str):
    """Returns an opaque lock handle on success, or None if another live
    process already holds this lock. Safe to call when the previous holder
    crashed without releasing - a dead owner's lock is reclaimed automatically."""
    for _ in range(2):  # one retry, only after reclaiming a confirmed-stale lock
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            return fd
        except FileExistsError:
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    holder_pid = int(f.read().strip())
                if is_pid_alive(holder_pid):
                    return None
            except (OSError, ValueError):
                pass  # unreadable/corrupt lock file - treat as stale too
            try:
                os.unlink(lock_path)
            except OSError:
                pass
    return None


def release_lock(handle) -> None:
    try:
        os.close(handle)
    except OSError:
        pass
