import os
import json
import re
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


def _mention_tokens(content: str):
    # Token-exact (not substring): "@codex-8763" must not match a session
    # literally named "codex-8763-2" in another project.
    return set(re.findall(r"@([A-Za-z0-9_.\-]+)", content))


def _mentions(content: str, name: str) -> bool:
    tokens = _mention_tokens(content)
    return name in tokens or "all" in tokens or "[stop]" in content


def fanout_targets(thread_id: str, sender: str, content: str) -> List[Tuple[str, int]]:
    """Who gets a socket push for this post. Active members (left=False)
    never do - they read the room stream via their own poll, and the
    socket is the out-of-room intercom, never a second room speaker.
    Only two paths knock: a soft-left member explicitly mentioned
    (left untouched - knock is a doorbell, not an enrollment), and a
    mesh session with no presence entry at all explicitly mentioned
    (one-shot, never enrolled - knock is not an invite)."""
    try:
        sessions = get_active_sessions()
    except Exception:
        sessions = []
    presence = read_thread_presence(thread_id)
    targets = []
    for name, info in presence.items():
        if not isinstance(info, dict):
            continue
        pid = info.get("pid")
        if name == sender or pid == os.getpid():
            continue
        if not isinstance(pid, int):
            continue
        if not info.get("left"):
            continue
        # A still-running peek already sees the mention via its own poll.
        if compat.is_pid_alive(pid):
            continue
        if not _mentions(content, name):
            continue
        targets.append((name, pid))
    pushed = {name for name, _ in targets}
    for token in _mention_tokens(content):
        # Mesh-wide summons: exact name, no presence entry (the presence
        # loop above already decided everyone inside the room), no enroll.
        if token == sender or token in pushed or token in presence:
            continue
        session = next((s for s in sessions if s.get("name") == token), None)
        if session is None:
            continue
        pid = session.get("pid")
        targets.append((token, pid if isinstance(pid, int) else -1))
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


def _append_system_event(thread_id: str, event: str, participant: str) -> None:
    """Room-stream lifecycle notice (0031): a state transition already
    decided by the caller, recorded as a plain log line. Raw locked
    append - never append_thread_message(), which would fanout."""
    try:
        ensure_dirs()
        path = get_thread_path(thread_id)
        lock_handle = _acquire_thread_lock(get_thread_lock_path(thread_id))
        if lock_handle is None:
            return
        try:
            last = _read_last_record(path)
            seq = (last.get("seq", 0) + 1) if last else 1
            verb = "joined the thread" if event == "join" else "left the thread"
            record = {
                "seq": seq,
                "ts": time.time(),
                "from": "system",
                "type": "event",
                "event": event,
                "who": participant,
                "content": f"{participant} {verb}",
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            _secure(path)
        finally:
            compat.release_lock(lock_handle)
    except Exception:
        pass  # ambient notice - must never fail the presence update


def touch_thread_presence(thread_id: str, participant: str, left: bool = False):
    """Records that `participant` is waiting right now, so a viewer can
    tell "who's listening" from "who has ever posted". `left` marks a peek
    (timeout-bounded): visible in the room, but gated from banter push.
    Emits one system join event on genuine activation only (0031)."""
    path = get_thread_presence_path(thread_id)
    lock_path = get_thread_lock_path(thread_id) + ".presence"
    lock_handle = _acquire_thread_lock(lock_path, timeout=0.5)
    if lock_handle is None:
        return  # best-effort - skip this update rather than write unlocked
    emit_join = False
    try:
        try:
            with open(path, "r", encoding="utf-8") as f:
                presence = json.load(f)
        except Exception:
            presence = {}
        prior = presence.get(participant)
        # Gate on the incoming value: a peek (left=True) is never an
        # arrival, even for a brand-new participant.
        emit_join = not left and (not isinstance(prior, dict) or prior.get("left"))
        presence[participant] = {"pid": os.getpid(), "last_seen": time.time(), "left": left}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(presence, f)
        _secure(path)
    finally:
        compat.release_lock(lock_handle)
    if emit_join:
        _append_system_event(thread_id, "join", participant)


def leave_thread_presence(thread_id: str, participant: str) -> bool:
    """Step out of the meeting (soft-leave): mark the presence entry left
    so banter skips this participant, while @mention/@all/[stop] can still
    knock (Phase 6). The cursor is deliberately left alone - rejoining
    (`agent-peer thread <id>`) re-touches presence and replays everything
    past last_seq as catch-up. Returns True if the entry exists."""
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
        entry = presence.get(participant)
        if not isinstance(entry, dict):
            return False
        emit_leave = not entry.get("left")
        entry["left"] = True
        with open(path, "w", encoding="utf-8") as f:
            json.dump(presence, f)
        _secure(path)
    finally:
        compat.release_lock(lock_handle)
    # Decided inside, emitted outside: never hold the presence lock
    # while taking the thread lock.
    if emit_leave:
        _append_system_event(thread_id, "leave", participant)
    return True


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
    could race a concurrent append and skip it before it's ever returned).
    Timeout-as-intent: a bounded wait is a peek (gated), only an indefinite
    wait arms full room presence."""
    touch_thread_presence(thread_id, participant, left=(timeout is not None))

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
