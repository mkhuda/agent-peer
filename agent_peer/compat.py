"""Every OS-specific call agent_peer makes, isolated here - nothing else
checks sys.platform. POSIX paths are the existing behavior, unchanged."""

import getpass
import os
import sys
import socket
import subprocess
import time

IS_WINDOWS = sys.platform == "win32"


def current_username() -> str:
    return os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser()


def secure_file(path: str) -> None:
    """Owner-only. POSIX: chmod 0600. Windows: icacls (verified live)."""
    if not IS_WINDOWS:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    else:
        user = current_username()
        subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", f"{user}:(F)"],
                        capture_output=True, check=False)


def secure_dir(path: str) -> None:
    """Owner-only, inherited by future children. POSIX: chmod 0700."""
    if not IS_WINDOWS:
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    else:
        user = current_username()
        subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)(F)"],
                        capture_output=True, check=False)


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


# --- Process fields for the parent-process walk (comm/ppid) and start time ---
# POSIX: ps, unchanged. Windows: CreateToolhelp32Snapshot + GetProcessTimes.

if not IS_WINDOWS:
    def get_process_field(pid: int, field: str) -> str:
        try:
            out = subprocess.check_output(["ps", "-o", f"{field}=", "-p", str(pid)], stderr=subprocess.DEVNULL)
            return out.decode("utf-8").strip()
        except Exception:
            return ""

    def get_process_start_time(pid: int) -> str:
        try:
            cmd = ["ps", "-o", "lstart=", "-p", str(pid)]
            env = dict(os.environ, LC_ALL="C", TZ="UTC")
            res = subprocess.check_output(cmd, env=env, stderr=subprocess.DEVNULL)
            return res.decode("utf-8").strip()
        except Exception:
            return ""
else:
    import time as _time

    class _PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32), ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_char * 260),
        ]

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]

    _TH32CS_SNAPPROCESS = 0x00000002

    def get_process_field(pid: int, field: str) -> str:
        kernel32 = ctypes.windll.kernel32
        snap = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return ""
        try:
            entry = _PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
            if not kernel32.Process32First(snap, ctypes.byref(entry)):
                return ""
            while True:
                if entry.th32ProcessID == pid:
                    if field == "comm":
                        return entry.szExeFile.decode(errors="replace")
                    if field == "ppid":
                        return str(entry.th32ParentProcessID)
                    return ""
                if not kernel32.Process32Next(snap, ctypes.byref(entry)):
                    return ""
        finally:
            kernel32.CloseHandle(snap)

    def get_process_start_time(pid: int) -> str:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            creation, exit_t, kernel_t, user_t = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
            ok = kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_t),
                                           ctypes.byref(kernel_t), ctypes.byref(user_t))
            if not ok:
                return ""
            ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            unix_ts = ticks / 10_000_000 - 11644473600  # FILETIME epoch is 1601-01-01
            return _time.strftime("%a %b %d %H:%M:%S %Y", _time.gmtime(unix_ts))
        except Exception:
            return ""
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
