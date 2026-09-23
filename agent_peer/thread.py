import os
import json
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .protocol import (
    get_thread_path,
    get_thread_cursor_path,
    get_thread_presence_path,
    get_thread_lock_path,
    ensure_dirs,
)
from .sender import send_message
from .registry import get_active_sessions
from . import compat

# A presence older than this is somebody who wandered off, not an active
# participant - fanout skips them (0028 Open Q1: spec suggests 5 minutes).
PRESENCE_ACTIVE_SECONDS = 300
# Total wall-time budget for one fanout across all concurrent workers.
FANOUT_BUDGET_SECONDS = 0.2


def _secure(path: str):
    if not compat.IS_WINDOWS:
        compat.secure_file(path)


def _acquire_thread_lock(lock_path: str, timeout: float = 2.0):
    """A sub-ms critical section should retry on brief contention, not refuse immediately."""
    t0 = time.time()
    while True:
        handle = compat.acquire_lock(lock_path)
        if handle is not None:
            return handle
        if time.time() - t0 >= timeout:
            return None
        time.sleep(0.01)


def _read_last_record(path: str) -> Optional[Dict[str, Any]]:
    """Reads only the final line, not the whole file, so seq assignment stays O(1)."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        chunk = b""
        while pos > 0:
            step = min(4096, pos)
            pos -= step
            f.seek(pos)
            chunk = f.read(step) + chunk
            if chunk.rstrip(b"\n").count(b"\n") >= 1 or pos == 0:
                break
        lines = [ln for ln in chunk.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    # A torn last line (e.g. a crash mid-write) must not reset seq to 1 -
    # fall back to the nearest valid line in this same tail chunk.
    for ln in reversed(lines):
        try:
            return json.loads(ln)
        except Exception:
            continue
    return None


def append_thread_message(thread_id: str, sender: str, content: str) -> Dict[str, Any]:
    """Locked append (unlike inbox.py's lock-free append): a long message can
    exceed the write size POSIX guarantees atomic, and Windows has no such guarantee."""
    ensure_dirs()
    path = get_thread_path(thread_id)
    lock_path = get_thread_lock_path(thread_id)
    lock_handle = _acquire_thread_lock(lock_path)
    if lock_handle is None:
        raise RuntimeError(f"Could not acquire the lock for thread '{thread_id}' - another append is stuck.")
    try:
        last = _read_last_record(path)
        seq = (last.get("seq", 0) + 1) if last else 1
        record = {"seq": seq, "ts": time.time(), "from": sender, "content": content}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        _secure(path)
    finally:
        compat.release_lock(lock_handle)
    # Append-first, push-second (Invariant #1): the record is already safe
    # on disk here, so the fanout below can only ever fail silently.
    fanout_thread_push(thread_id, record["seq"], sender, content)
    return record


def _mentions(content: str, name: str) -> bool:
    return f"@{name}" in content or "@all" in content or "[stop]" in content


def _session_busy(sessions, name: str, pid: int) -> bool:
    for session in sessions:
        if session.get("name") == name or session.get("pid") == pid:
            return session.get("status") == "busy"
    return False


def fanout_targets(thread_id: str, sender: str, content: str) -> List[Tuple[str, int]]:
    """Active presence entries eligible for a native push: not the sender,
    not this process, seen recently. Resolved by name at push time (the
    presence pid is the waiter/join process, never a registered session).
    A busy participant is only pushed on @name/@all/[stop] (anti-bombing)."""
    now = time.time()
    try:
        sessions = get_active_sessions()
    except Exception:
        sessions = []
    targets = []
    for name, info in read_thread_presence(thread_id).items():
        if not isinstance(info, dict):
            continue
        pid = info.get("pid")
        if name == sender or pid == os.getpid():
            continue
        if not isinstance(pid, int):
            continue
        if now - info.get("last_seen", 0) > PRESENCE_ACTIVE_SECONDS:
            continue
        if _session_busy(sessions, name, pid) and not _mentions(content, name):
            continue
        targets.append((name, pid))
    return targets


def _push_one(name: str, pid: int, frame: str, sender: str):
    # Name first: presence pids belong to waiter/join processes, which are
    # never registered sessions - only the participant's listener is, and it
    # registers under this same name by convention. PID is a free fallback.
    for target in (name, str(pid)):
        try:
            send_message(target, frame, from_name=sender)
            return
        except Exception:
            continue  # a dead/unreachable target must never fail the poster


def fanout_thread_push(thread_id: str, seq: int, sender: str, content: str):
    """Best-effort native wake for active participants. Daemon workers plus
    a bounded main-thread wait: a hung socket can delay a post by at most
    FANOUT_BUDGET_SECONDS and can never wedge interpreter exit. Never raises."""
    try:
        targets = fanout_targets(thread_id, sender, content)
        if not targets:
            return
        frame = f"[thread: {thread_id} #{seq} from {sender}]: {content}"
        workers = []
        for name, pid in targets:
            worker = threading.Thread(target=_push_one, args=(name, pid, frame, sender), daemon=True)
            worker.start()
            workers.append(worker)
        deadline = time.time() + FANOUT_BUDGET_SECONDS
        for worker in workers:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            worker.join(timeout=remaining)
    except Exception:
        pass


def read_thread(thread_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    path = get_thread_path(thread_id)
    if not os.path.exists(path):
        return []
    messages = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except Exception:
                continue
    if limit:
        return messages[-limit:]
    return messages


def _read_thread_cursor(thread_id: str, participant: str) -> int:
    """A new participant's cursor starts at 0 (full backlog), not "now" like
    inbox.py - the foreman typically posts before calling an agent to join."""
    path = get_thread_cursor_path(thread_id, participant)
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(json.load(f).get("last_seq", 0))
    except Exception:
        return 0


def _write_thread_cursor(thread_id: str, participant: str, seq: int):
    ensure_dirs()
    path = get_thread_cursor_path(thread_id, participant)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"last_seq": seq}, f)
    _secure(path)


def touch_thread_presence(thread_id: str, participant: str):
    """Records that `participant` is actively waiting right now, so a viewer
    can tell "who's listening" from "who has ever posted"."""
    path = get_thread_presence_path(thread_id)
    lock_path = get_thread_lock_path(thread_id) + ".presence"
    lock_handle = _acquire_thread_lock(lock_path, timeout=0.5)
    if lock_handle is None:
        return  # best-effort - skip this update rather than write unlocked
    try:
        try:
            with open(path, "r", encoding="utf-8") as f:
                presence = json.load(f)
        except Exception:
            presence = {}
        presence[participant] = {"pid": os.getpid(), "last_seen": time.time()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(presence, f)
        _secure(path)
    finally:
        compat.release_lock(lock_handle)


def leave_thread_presence(thread_id: str, participant: str) -> bool:
    """Step out of the meeting: drop the presence entry so fanout skips
    this participant. The cursor is deliberately left alone - rejoining
    (`agent-peer thread <id>`) re-touches presence and replays everything
    past last_seq as catch-up. Returns True if an entry was removed."""
    path = get_thread_presence_path(thread_id)
    lock_path = get_thread_lock_path(thread_id) + ".presence"
    lock_handle = _acquire_thread_lock(lock_path, timeout=0.5)
    if lock_handle is None:
        return False
    try:
        try:
            with open(path, "r", encoding="utf-8") as f:
                presence = json.load(f)
        except Exception:
            return False
        if participant not in presence:
            return False
        del presence[participant]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(presence, f)
        _secure(path)
        return True
    finally:
        compat.release_lock(lock_handle)


def read_thread_presence(thread_id: str) -> Dict[str, Any]:
    path = get_thread_presence_path(thread_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_thread_unread(thread_id: str, participant: str) -> List[Dict[str, Any]]:
    """Self-echo filtered: a participant must never see its own just-appended
    message as "new" - that's an infinite reply-to-self loop otherwise."""
    cursor = _read_thread_cursor(thread_id, participant)
    return [
        m for m in read_thread(thread_id)
        if m.get("seq", 0) > cursor and m.get("from") != participant
    ]


def wait_for_thread_message(
    thread_id: str,
    participant: str,
    timeout: Optional[float] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Immediate-backlog-then-poll, like inbox.py's wait_for_message but
    shared. Cursor advances to unread[-1]'s seq (not a fresh re-read, which
    could race a concurrent append and skip it before it's ever returned)."""
    touch_thread_presence(thread_id, participant)

    unread = get_thread_unread(thread_id, participant)
    if unread:
        _write_thread_cursor(thread_id, participant, unread[-1]["seq"])
        return unread

    t0 = time.time()
    while True:
        unread = get_thread_unread(thread_id, participant)
        if unread:
            _write_thread_cursor(thread_id, participant, unread[-1]["seq"])
            return unread
        if timeout is not None and (time.time() - t0) >= timeout:
            return None
        time.sleep(0.1)
