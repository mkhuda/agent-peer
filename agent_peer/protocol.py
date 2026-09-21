import os
import json
import re
import subprocess
import secrets
import hashlib
from typing import Optional

CLAUDE_CONFIG_DIR = os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude"))
SESSIONS_DIR = os.path.join(CLAUDE_CONFIG_DIR, "sessions")
SOCKET_DIR = "/tmp/cc-socks"
AGENT_PEER_DIR = os.path.expanduser("~/.agent-peer")
INBOX_FILE = os.path.join(AGENT_PEER_DIR, "inbox.jsonl")
INBOXES_DIR = os.path.join(AGENT_PEER_DIR, "inboxes")
CURSORS_DIR = os.path.join(AGENT_PEER_DIR, "cursors")
LOCKS_DIR = os.path.join(AGENT_PEER_DIR, "locks")

def ensure_dirs():
    # Only our own directories are locked to 0700 - SOCKET_DIR/SESSIONS_DIR
    # belong to Claude Code's own protocol.
    for d in (AGENT_PEER_DIR, INBOXES_DIR, CURSORS_DIR, LOCKS_DIR):
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    os.makedirs(SOCKET_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)

def get_session_inbox_path(session_id_or_name: str) -> str:
    ensure_dirs()
    return os.path.join(INBOXES_DIR, f"{session_id_or_name}.jsonl")

def get_cursor_path(session_id_or_name: str = None) -> str:
    ensure_dirs()
    key = str(session_id_or_name) if session_id_or_name else "_global"
    return os.path.join(CURSORS_DIR, f"{key}.json")

def get_lock_path(session_id_or_name: str = None) -> str:
    ensure_dirs()
    key = str(session_id_or_name) if session_id_or_name else "_global"
    return os.path.join(LOCKS_DIR, f"{key}.lock")

def get_proc_start(pid: int) -> str:
    """Get process start time matching Claude Code's Lue() format: LC_ALL=C TZ=UTC ps -o lstart= -p <pid>"""
    try:
        cmd = ["ps", "-o", "lstart=", "-p", str(pid)]
        env = dict(os.environ, LC_ALL="C", TZ="UTC")
        res = subprocess.check_output(cmd, env=env, stderr=subprocess.DEVNULL)
        return res.decode("utf-8").strip()
    except Exception:
        return ""

def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False

def get_harness_cwd(pid: int):
    """The harness process's OWN cwd (tracked by the OS), not the cwd of
    whichever subshell/tool-call happens to invoke 'agent-peer listen' - a
    long-running TUI's cwd never drifts just because an individual tool call
    runs elsewhere. Returns None if unavailable (unsupported OS, no
    permission, or the process is gone)."""
    try:
        proc_cwd = f"/proc/{pid}/cwd"
        if os.path.isdir("/proc"):
            return os.readlink(proc_cwd)
    except OSError:
        return None
    try:
        out = subprocess.check_output(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8")
        for line in out.splitlines():
            if line.startswith("n"):
                return line[1:]
    except Exception:
        pass
    return None

# Generic shell/interpreter process names to skip while walking up the parent
# chain looking for the actual harness (agy, pi, opencode, ...) that invoked us.
_GENERIC_PROC_NAMES = {
    "zsh", "bash", "sh", "dash", "tcsh", "csh", "ksh", "fish",
    "login", "env", "sudo", "su", "node", "uv", "uvx", "python", "python3",
}

def _ps_field(pid: int, field: str) -> str:
    try:
        out = subprocess.check_output(["ps", "-o", f"{field}=", "-p", str(pid)], stderr=subprocess.DEVNULL)
        return out.decode("utf-8").strip()
    except Exception:
        return ""

# Strips a trailing "-bin-<version>" some harness binaries embed in their
# own process name, e.g. "muse-bin-1.3.0-R3401.1" -> "muse".
_VERSIONED_BIN_RE = re.compile(r"^([a-z0-9]+)-bin(-.*)?$", re.IGNORECASE)

def _normalize_harness_name(name: str) -> str:
    m = _VERSIONED_BIN_RE.match(name)
    return m.group(1).lower() if m else name

def detect_harness_identity(max_depth: int = 6):
    """Walk up the parent-process chain past generic shells to find the
    calling harness. Returns (name, pid), or (None, None) if none found."""
    pid = os.getppid()
    for _ in range(max_depth):
        if pid <= 1:
            break
        comm = _ps_field(pid, "comm")
        base = os.path.basename(comm) if comm else ""
        if base and base.lower() not in _GENERIC_PROC_NAMES and not base.lower().startswith("python3."):
            return _normalize_harness_name(base), pid
        ppid_str = _ps_field(pid, "ppid")
        if not ppid_str.isdigit():
            break
        pid = int(ppid_str)
    return None, None

def harness_session_uid() -> Optional[str]:
    """
    Stable id of the harness *session* hosting this process, when the
    harness exposes one. Unlike a PID this survives across the many short
    tool-call subprocesses of one session, so a second `listen` from the
    same session is recognizable as a duplicate rather than a new peer.
    Returns None where no harness session id is available (current behavior
    is kept untouched there).
    """
    thread = os.environ.get("CODEX_THREAD_ID")
    if thread:
        return f"codex:{thread}"
    if os.environ.get("HERDR_ENV") == "1" and os.environ.get("HERDR_PANE_ID"):
        return f"herdr:{os.environ['HERDR_PANE_ID']}"
    return None

def auto_session_name() -> str:
    """Best-effort per-harness session name, e.g. 'agy-33402', 'pi-1234'."""
    name, pid = detect_harness_identity()
    if name:
        return f"{name}-{pid}"
    return f"agent-{os.getpid()}"

def generate_peer_token() -> str:
    """Generate 32 hex chars (16 bytes) peer token."""
    return secrets.token_hex(16)

def generate_key_filename(pid: int, sock_path: str) -> str:
    """Generate <pid>.<sha256(sock_path)>.key filename matching Claude Code's ZB()."""
    h = hashlib.sha256(sock_path.encode("utf-8")).hexdigest()
    return f"{pid}.{h}.key"

def format_auth_frame(token: str) -> str:
    return json.dumps({"type": "auth", "token": token}) + "\n"

def format_user_frame(
    content: str,
    from_name: str = "agent",
    from_cwd: str = None,
    priority: str = "now",
    to_name: str = None,
    to_pid: int = None
) -> str:
    payload = {
        "type": "user",
        "priority": priority,
        "from": from_name,
        "message": {
            "content": content
        }
    }
    if to_name:
        payload["to"] = to_name
    if to_pid:
        payload["to_pid"] = to_pid
    if from_cwd:
        payload["from_cwd"] = from_cwd
    return json.dumps(payload) + "\n"
