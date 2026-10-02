import os
import glob
import json
import re
from typing import List, Dict, Optional, Tuple

from . import compat
from .protocol import AGENT_SESSIONS_DIR, SESSIONS_DIR, SOCKET_DIR, atomic_write_json, is_pid_alive, get_proc_start
from .codex_queue import queue_state

_json_decoder = json.JSONDecoder()


def _load_json_lenient(raw: str):
    """A session file with valid JSON plus trailing garbage (confirmed live:
    a native Claude Code session's own write left a stray extra '}') must
    not make the whole session vanish - recover the leading object instead
    of raising, since agent-peer doesn't control how that file gets written."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        obj, _ = _json_decoder.raw_decode(raw)
        return obj

PID_JSON_RE = re.compile(r"^(\d+)\.json$")
KEY_FILE_RE = re.compile(r"^(\d+)\.[0-9a-f]{64}\.key$")

# agy's statusline.sh writes agent_state into agy_status.json on each
# render - a global snapshot of whichever agy session last rendered.
_AGY_LIVE_STATUS_MAX_AGE_S = 90
_AGY_STATE_TO_STATUS = {"working": "busy", "idle": "idle"}


def _apply_agy_live_status(sessions: List[Dict]) -> None:
    try:
        from .agy_status import read_agy_status
        payload, age_seconds, error = read_agy_status()
    except Exception:
        return
    if error or not payload or age_seconds is None or age_seconds > _AGY_LIVE_STATUS_MAX_AGE_S:
        return

    live_status = _AGY_STATE_TO_STATUS.get(payload.get("agent_state"))
    if not live_status:
        return

    cwd = payload.get("cwd") or (payload.get("workspace") or {}).get("current_dir")
    if not cwd:
        return

    for s in sessions:
        if s.get("agentType") != "AGY" or s.get("cwd") != cwd:
            continue
        if s.get("status") == "new-msg":
            continue  # don't hide an unread message behind a busy/idle refresh
        s["status"] = live_status

# Codex reports no turn state of its own, but it only takes a queued message
# when a turn ends (a queued item waited 20 min behind one long tool call, live).
# So an item that has sat unconsumed for a few seconds means Codex is mid-turn.
# A working Codex with an empty queue still reads idle - this is a partial signal.
_CODEX_BUSY_MIN_QUEUE_AGE_S = 10


def _apply_codex_queue_status(sessions: List[Dict]) -> None:
    import time

    now = time.time()
    for s in sessions:
        if s.get("agentType") != "CODEX" or not s.get("alive") or not s.get("codexThreadId"):
            continue
        state = queue_state(s["codexThreadId"])
        if state and state[0] >= 1 and state[1]:
            s["queued"] = state[0]
            s["queuedOldestAgeS"] = int(now - state[1])
        if s.get("status") == "new-msg":
            continue
        if state and state[0] >= 1 and state[1] and now - state[1] >= _CODEX_BUSY_MIN_QUEUE_AGE_S:
            s["status"] = "busy"

def session_paths(pid: int, key_filename: str) -> Tuple[str, str]:
    """(json_path, key_path) a listener with this pid registers under."""
    return os.path.join(SESSIONS_DIR, f"{pid}.json"), os.path.join(SESSIONS_DIR, key_filename)


def ensure_sessions_dir() -> None:
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def write_key(key_path: str, key_data: Dict) -> None:
    with open(key_path, "w", encoding="utf-8") as f:
        json.dump(key_data, f)
    compat.secure_file(key_path)


def write_session(json_path: str, meta: Dict) -> None:
    atomic_write_json(json_path, meta)


def update_session(json_path: str, fields: Dict) -> bool:
    """Merge `fields` into an existing entry; False when the entry is gone."""
    if not os.path.exists(json_path):
        return False
    with open(json_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta.update(fields)
    atomic_write_json(json_path, meta)
    return True


def remove_session(json_path: Optional[str], key_path: Optional[str]) -> List[str]:
    """Delete an entry's json and key file; returns the paths actually removed."""
    removed = []
    for p in (json_path, key_path):
        if p and (os.path.exists(p) or os.path.islink(p)):
            try:
                os.unlink(p)
                removed.append(p)
            except OSError:
                pass
    return removed


def get_session_agent_type(session_data: dict) -> str:
    """Determine if session is 'AGY' (Google Antigravity) or 'Claude' (Claude Code)."""
    if session_data.get("agentType"):
        return session_data["agentType"].upper()
    name = (session_data.get("name") or "").lower()
    if "antigravity" in name or "agy" in name:
        return "AGY"
    return "Claude"

def _registry_dirs() -> List[Tuple[str, str]]:
    """(directory, source) in precedence order; AGENT_PEER_REGISTRY=legacy reads only Claude's."""
    if os.environ.get("AGENT_PEER_REGISTRY") == "legacy":
        return [(SESSIONS_DIR, "claude")]
    return [(AGENT_SESSIONS_DIR, "agent-peer"), (SESSIONS_DIR, "claude")]


def _read_entries(directory: str, source: str) -> List[Dict]:
    entries = []
    try:
        names = os.listdir(directory)
    except OSError:
        return entries

    for fname in names:
        if source == "claude":
            m = PID_JSON_RE.match(fname)
            if not m:
                continue
            file_pid = int(m.group(1))
        elif fname.endswith(".json") and ".tmp." not in fname:
            file_pid = None
        else:
            continue

        json_path = os.path.join(directory, fname)
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = _load_json_lenient(f.read())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue

        pid = file_pid if file_pid is not None else data.get("pid")
        if not isinstance(pid, int):
            continue

        data["pid"] = pid
        data["alive"] = is_pid_alive(pid)
        data["jsonPath"] = json_path
        data["source"] = source
        data["agentType"] = get_session_agent_type(data)

        if source == "claude":
            key_files = glob.glob(os.path.join(directory, f"{pid}.*.key"))
            key_file_path = key_files[0] if key_files else None
        else:
            key_file_path = json_path[: -len(".json")] + ".key"
        peer_token = None
        if key_file_path and os.path.exists(key_file_path):
            try:
                with open(key_file_path, "r", encoding="utf-8") as f:
                    peer_token = json.load(f).get("peerToken")
            except Exception:
                pass

        data["peerToken"] = peer_token
        data["keyFile"] = key_file_path if key_file_path and os.path.exists(key_file_path) else None

        sock_path = data.get("messagingSocketPath") or os.path.join(SOCKET_DIR, f"{pid}.sock")
        data["messagingSocketPath"] = sock_path
        data["socketExists"] = os.path.exists(sock_path)
        entries.append(data)
    return entries


# Status fields follow whichever copy of one listener's entry was written last.
_STATUS_FIELDS = ("status", "statusUpdatedAt", "title", "updatedAt")


def _merge_entries(entries: List[Dict]) -> List[Dict]:
    """One entry per listener (`sessionId` on the same pid): the first copy wins, newer status wins."""
    by_listener: Dict[Tuple[str, int], Dict] = {}
    merged = []
    for entry in entries:
        sid = entry.get("sessionId")
        kept = by_listener.get((sid, entry["pid"])) if sid else None
        if kept is None:
            if sid:
                by_listener[(sid, entry["pid"])] = entry
            merged.append(entry)
        elif (entry.get("statusUpdatedAt") or 0) > (kept.get("statusUpdatedAt") or 0):
            for field in _STATUS_FIELDS:
                if field in entry:
                    kept[field] = entry[field]

    # One pid, one entry: after a pid is reused only the newer listener counts.
    newest: Dict[int, Dict] = {}
    for entry in merged:
        cur = newest.get(entry["pid"])
        if cur is None or (entry.get("startedAt") or 0) > (cur.get("startedAt") or 0):
            newest[entry["pid"]] = entry
    return [e for e in merged if newest[e["pid"]] is e]


def get_active_sessions() -> List[Dict]:
    """Discover sessions registered in ~/.agent-peer/sessions and in Claude's ~/.claude/sessions."""
    entries = []
    for directory, source in _registry_dirs():
        entries.extend(_read_entries(directory, source))
    sessions = _merge_entries(entries)

    _apply_agy_live_status(sessions)
    _apply_codex_queue_status(sessions)

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
