import os
import json
import time
from typing import List, Dict, Any, Optional

from .protocol import INBOX_FILE, get_session_inbox_path, ensure_dirs

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

def wait_for_message(session: Optional[str] = None, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
    initial_count = len(read_inbox(session=session))
    t0 = time.time()
    while True:
        msgs = read_inbox(session=session)
        if len(msgs) > initial_count:
            return msgs[-1]
        if timeout is not None and (time.time() - t0) >= timeout:
            return None
        time.sleep(0.1)
