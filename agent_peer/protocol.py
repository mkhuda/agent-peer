import os
import json
import subprocess
import secrets
import hashlib

CLAUDE_CONFIG_DIR = os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude"))
SESSIONS_DIR = os.path.join(CLAUDE_CONFIG_DIR, "sessions")
SOCKET_DIR = "/tmp/cc-socks"
AGENT_PEER_DIR = os.path.expanduser("~/.agent-peer")
INBOX_FILE = os.path.join(AGENT_PEER_DIR, "inbox.jsonl")
INBOXES_DIR = os.path.join(AGENT_PEER_DIR, "inboxes")

def ensure_dirs():
    os.makedirs(AGENT_PEER_DIR, exist_ok=True)
    os.makedirs(INBOXES_DIR, exist_ok=True)
    os.makedirs(SOCKET_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)

def get_session_inbox_path(session_id_or_name: str) -> str:
    ensure_dirs()
    return os.path.join(INBOXES_DIR, f"{session_id_or_name}.jsonl")

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

def generate_peer_token() -> str:
    """Generate 32 hex chars (16 bytes) peer token."""
    return secrets.token_hex(16)

def generate_key_filename(pid: int, sock_path: str) -> str:
    """Generate <pid>.<sha256(sock_path)>.key filename matching Claude Code's ZB()."""
    h = hashlib.sha256(sock_path.encode("utf-8")).hexdigest()
    return f"{pid}.{h}.key"

def format_auth_frame(token: str) -> str:
    return json.dumps({"type": "auth", "token": token}) + "\n"

def format_user_frame(content: str, from_name: str = "antigravity", from_sock: str = None, priority: str = "now") -> str:
    origin_from = f"uds:{from_sock}" if from_sock else from_name
    payload = {
        "type": "user",
        "priority": priority,
        "from": origin_from,
        "message": {
            "content": content
        }
    }
    return json.dumps(payload) + "\n"
