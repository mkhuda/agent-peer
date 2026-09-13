import os
import glob
import json
import re
from typing import List, Dict, Optional, Tuple

from .protocol import SESSIONS_DIR, SOCKET_DIR, is_pid_alive, get_proc_start

PID_JSON_RE = re.compile(r"^(\d+)\.json$")
KEY_FILE_RE = re.compile(r"^(\d+)\.[0-9a-f]{64}\.key$")

def get_session_agent_type(session_data: dict) -> str:
    """Determine if session is 'AGY' (Google Antigravity) or 'Claude' (Claude Code)."""
    if session_data.get("agentType"):
        return session_data["agentType"].upper()
    name = (session_data.get("name") or "").lower()
    if "antigravity" in name or "agy" in name:
        return "AGY"
    return "Claude"

def get_active_sessions() -> List[Dict]:
    """Discover all active sessions registered in ~/.claude/sessions/"""
    sessions = []
    if not os.path.exists(SESSIONS_DIR):
        return sessions

    for fname in os.listdir(SESSIONS_DIR):
        m = PID_JSON_RE.match(fname)
        if not m:
            continue
        pid = int(m.group(1))
        
        # Check if process is alive
        alive = is_pid_alive(pid)
        
        json_path = os.path.join(SESSIONS_DIR, fname)
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        if not isinstance(data, dict):
            continue

        data["pid"] = pid
        data["alive"] = alive
        data["agentType"] = get_session_agent_type(data)

        # Find key file
        key_files = glob.glob(os.path.join(SESSIONS_DIR, f"{pid}.*.key"))
        peer_token = None
        key_file_path = None
        if key_files:
            key_file_path = key_files[0]
            try:
                with open(key_file_path, "r", encoding="utf-8") as f:
                    key_data = json.load(f)
                    peer_token = key_data.get("peerToken")
            except Exception:
                pass

        data["peerToken"] = peer_token
        data["keyFile"] = key_file_path

        # Check socket
        sock_path = data.get("messagingSocketPath") or os.path.join(SOCKET_DIR, f"{pid}.sock")
        data["messagingSocketPath"] = sock_path
        data["socketExists"] = os.path.exists(sock_path)

        sessions.append(data)

    # Sort by name, then pid
    sessions.sort(key=lambda s: (s.get("name") or "", s.get("pid", 0)))
    return sessions

def resolve_session(target: str) -> Tuple[Dict, str, str]:
    """
    Resolve target string (name, alias, or PID) to (session_dict, socket_path, peer_token).
    Raises ValueError if target is not found or ambiguous.
    """
    sessions = get_active_sessions()
    
    # Try exact PID match
    if target.isdigit():
        target_pid = int(target)
        for s in sessions:
            if s["pid"] == target_pid:
                if not s["alive"]:
                    raise ValueError(f"Session with PID {target_pid} ({s.get('name')}) is no longer alive.")
                token = s.get("peerToken")
                if not token:
                    raise ValueError(f"Session PID {target_pid} has no peerToken in {SESSIONS_DIR}.")
                return s, s["messagingSocketPath"], token
        raise ValueError(f"No registered session found for PID {target_pid}.")

    # Try exact name match
    target_lower = target.lower().strip()
    exact_matches = [
        s for s in sessions
        if (s.get("name") or "").lower().strip() == target_lower and s["alive"]
    ]
    if len(exact_matches) == 1:
        s = exact_matches[0]
        token = s.get("peerToken")
        if not token:
            raise ValueError(f"Session '{s.get('name')}' (PID {s['pid']}) has no peerToken.")
        return s, s["messagingSocketPath"], token
    elif len(exact_matches) > 1:
        names = [f"PID {m['pid']}" for m in exact_matches]
        raise ValueError(f"Ambiguous target '{target}'. Multiple active sessions share this name: {', '.join(names)}. Target by PID instead (e.g. agent-peer send <pid>).")

    # Try partial name match / alias
    matches = []
    for s in sessions:
        name = (s.get("name") or "").lower().strip()
        if (target_lower in name or target_lower == name.replace("-", "").replace("_", "")) and s["alive"]:
            matches.append(s)

    if len(matches) == 1:
        s = matches[0]
        token = s.get("peerToken")
        if not token:
            raise ValueError(f"Session '{s.get('name')}' (PID {s['pid']}) has no peerToken.")
        return s, s["messagingSocketPath"], token

    if len(matches) > 1:
        names = [f"'{m.get('name')}' (PID {m['pid']})" for m in matches]
        raise ValueError(f"Ambiguous target '{target}'. Multiple active matches: {', '.join(names)}")

    available = [f"{s.get('name')} (PID {s['pid']})" for s in sessions if s['alive']]
    raise ValueError(f"Session '{target}' not found. Active sessions: {', '.join(available) if available else 'None'}")
