"""Every OS-specific call agent_peer makes, isolated here - nothing else
checks sys.platform. POSIX paths are the existing behavior, unchanged."""

import os
import sys
import socket
import subprocess
import time

IS_WINDOWS = sys.platform == "win32"


# --- Transport: bind/accept/connect over the mesh's own two-frame protocol ---
# POSIX: socket.AF_UNIX, unchanged. Windows: Named Pipes via multiprocessing.connection.

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
            # _recv_bytes/_send_bytes (private, verified live) skip the public
            # API's 4-byte length prefix - keeps the wire raw, matching AF_UNIX.
            if self._timeout is not None:
                if not self._conn.poll(self._timeout):
                    raise TimeoutError("timed out waiting for data")
            try:
                buf = self._conn._recv_bytes()
            except EOFError:
                return b""
            return buf.getvalue()

        def sendall(self, data: bytes) -> None:
            self._conn._send_bytes(data)

        def settimeout(self, seconds) -> None:
            self._timeout = seconds

        def close(self) -> None:
            try:
                self._conn.close()
            except OSError:
                pass

    class Listener:
        def __init__(self, address: str):
            # `address` is a short id, not a path - \\.\pipe\ is added here.
            self.address = address
            self._pipe_name = r"\\.\pipe\%s" % address
            self._listener = None

        def bind(self) -> None:
            pass  # construction is deferred to listen() to match POSIX's split

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
        # A busy pipe instance rejects a concurrent connect() outright -
        # retry within the deadline instead of failing on one busy instant.
        pipe_name = r"\\.\pipe\%s" % address
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                conn = _MPClient(pipe_name)
                wrapped = Connection(conn)
                wrapped.settimeout(timeout)
                return wrapped
            except OSError as e:
                last_error = e
                time.sleep(0.05)
        raise last_error if last_error else OSError(f"could not connect to {pipe_name}")


# --- Process liveness: os.kill(pid, 0) on POSIX, ctypes kernel32 on Windows ---

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


# --- File locking: O_CREAT|O_EXCL (fcntl doesn't exist on Windows) ---
# Unlike flock, a lock FILE isn't auto-released on crash - acquire_lock stores
# its pid and reclaims a lock whose owner is no longer alive.

class LockHandle:
    """fd + path, so release_lock can unlink - a stale pid left on disk
    could later match a reused pid and wedge the lock forever."""
    __slots__ = ("fd", "path")

    def __init__(self, fd: int, path: str):
        self.fd = fd
        self.path = path


def acquire_lock(lock_path: str):
    """Returns a LockHandle on success, or None if another live process
    already holds this lock. Safe to call when the previous holder crashed
    without releasing - a dead owner's lock is reclaimed automatically."""
    for attempt in range(3):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            return LockHandle(fd, lock_path)
        except FileExistsError:
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if not content:
                    # Owner's os.open() beat us here, its write() hasn't landed
                    # yet - wait briefly rather than deleting a valid lock.
                    if attempt < 2:
                        time.sleep(0.03)
                        continue
                    return None
                holder_pid = int(content)
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
    if handle is None:
        return
    try:
        os.close(handle.fd)
    except OSError:
        pass
    try:
        os.unlink(handle.path)
    except OSError:
        pass
