import os
import sys
import json
import time
from typing import List, Dict, Any, Optional

from .protocol import INBOX_FILE, get_session_inbox_path, get_cursor_path, ensure_dirs

def _secure(path: str):
    """Restrict to owner-only, matching the socket/key file permissions - these
    files carry full inter-agent message content in plain text (see
    docs/agent-peer-weaknesses-report.md)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

def append_inbox(
    record: Dict[str, Any],
    session_name: Optional[str] = None,
    session_pid: Optional[int] = None
):
    ensure_dirs()
    record["received_at"] = time.time()
    record["received_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record["received_at"]))
    if session_name:
        record["recipient_name"] = session_name
    if session_pid:
        record["recipient_pid"] = session_pid

    # 1. Global audit log
    with open(INBOX_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    _secure(INBOX_FILE)

    # 2. Session-specific inboxes
    targets = set()
    if session_name:
        targets.add(str(session_name))
    if session_pid:
        targets.add(str(session_pid))
    for t in targets:
        sp = get_session_inbox_path(t)
        with open(sp, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        _secure(sp)

def read_inbox(session: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    path = get_session_inbox_path(session) if session else INBOX_FILE
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

def clear_inbox(session: Optional[str] = None):
    path = get_session_inbox_path(session) if session else INBOX_FILE
    if os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
    # Reset the cursor too, so "clear" means forget everything - not just empty
    # the log while leaving stale read-state behind. Written to now(), NOT
    # deleted: deleting it would reopen the exact gap mark_session_start() was
    # added to close - the next _read_cursor() call would lazily default the
    # cursor to whenever that read happens to occur, so anything sent between
    # this clear and the next 'wait' would be silently swallowed as "old".
    _write_cursor(session, time.time())

def _read_cursor(session: Optional[str] = None) -> float:
    path = get_cursor_path(session)
    if not os.path.exists(path):
        now = time.time()
        _write_cursor(session, now)
        return now
    try:
        with open(path, "r", encoding="utf-8") as f:
            return float(json.load(f).get("last_read_at", 0))
    except Exception:
        # Corrupt/unparseable cursor - fail toward replaying everything rather
        # than toward silently treating all unread messages as already-read.
        print(f"⚠️  Cursor file '{path}' is corrupt/unreadable - treating this session as never having read anything.", file=sys.stderr)
        return 0.0

def _write_cursor(session: Optional[str] = None, ts: Optional[float] = None):
    ensure_dirs()
    path = get_cursor_path(session)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"last_read_at": ts if ts is not None else time.time()}, f)
    _secure(path)

def mark_session_start(session: Optional[str] = None):
    """
    Initialize this session's unread cursor to now. Call this once the listener
    is actually ready to receive (end of setup(), before accept() ever runs) so
    a message sent before this session's first-ever `wait` call still counts as
    unread, instead of being silently swallowed by the lazy cursor-init fallback
    in _read_cursor (which exists for sessions that predate this feature).
    """
    _write_cursor(session, time.time())

def get_unread(session: Optional[str] = None) -> List[Dict[str, Any]]:
    """Messages received after this session's cursor, oldest first."""
    cursor = _read_cursor(session)
    return [m for m in read_inbox(session=session) if m.get("received_at", 0) > cursor]

def wait_for_message(session: Optional[str] = None, timeout: Optional[float] = None) -> Optional[List[Dict[str, Any]]]:
    """
    Return any unread messages immediately (backlog merged in one call), advancing
    the cursor. If there's no backlog, block-poll for the next arrival instead.
    """
    unread = get_unread(session=session)
    if unread:
        _write_cursor(session, unread[-1]["received_at"])
        return unread

    t0 = time.time()
    while True:
        unread = get_unread(session=session)
        if unread:
            _write_cursor(session, unread[-1]["received_at"])
            return unread
        if timeout is not None and (time.time() - t0) >= timeout:
            return None
        time.sleep(0.1)
