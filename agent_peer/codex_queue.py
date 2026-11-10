"""A Codex session's native message queue, read from the database Codex keeps under ~/.codex.

Codex offers no CLI to read or empty its queue. The schema is Codex-internal: readers return
None on any surprise; purge_queue raises, since it writes."""
import glob
import json
import os
import re
import sqlite3
import time
from typing import List, Optional, Tuple


def _queue_db() -> Optional[str]:
    def version(path: str) -> int:
        m = re.search(r"queue_(\d+)\.sqlite$", path)
        return int(m.group(1)) if m else -1

    dbs = sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".codex", "queue_*.sqlite")), key=version)
    return dbs[-1] if dbs else None


def thread_cwd(codex_thread_id: Optional[str]) -> Optional[str]:
    """The working directory Codex recorded for a thread, or None. A shared Codex daemon's own
    cwd says nothing about its threads."""
    if not codex_thread_id:
        return None
    def version(path: str) -> int:
        m = re.search(r"state_(\d+)\.sqlite$", path)
        return int(m.group(1)) if m else -1

    dbs = sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".codex", "state_*.sqlite")), key=version, reverse=True)
    for db in dbs:  # newest first; a thread started before a schema bump lives in an older file
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.3)
            try:
                row = con.execute("SELECT cwd FROM threads WHERE id = ?", (codex_thread_id,)).fetchone()
            finally:
                con.close()
        except Exception:
            continue
        if row and isinstance(row[0], str) and row[0]:
            return row[0]
    return None


def queue_state(codex_thread_id: Optional[str]) -> Optional[Tuple[int, Optional[float]]]:
    """(items waiting, created-at epoch seconds of the oldest) for one Codex
    thread, or None when it cannot be read."""
    if not codex_thread_id:
        return None
    try:
        db = _queue_db()
        if not db:
            return None
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.3)
        try:
            n, oldest_ms = con.execute(
                "SELECT COUNT(*), MIN(created_at_ms) FROM queued_items WHERE thread_id = ?",
                (codex_thread_id,),
            ).fetchone()
        finally:
            con.close()
        return int(n), (oldest_ms / 1000.0 if oldest_ms else None)
    except Exception:
        return None


def _item_text(payload_json: str) -> str:
    try:
        content = json.loads(payload_json).get("UserInput", {}).get("content", [])
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    except Exception:
        return payload_json


def purge_queue(codex_thread_id: str) -> List[Tuple[float, str]]:
    """Delete every message waiting for one Codex thread; returns (created-at epoch s, text) of each."""
    db = _queue_db()
    if not db:
        raise FileNotFoundError("no Codex queue database under ~/.codex")
    con = sqlite3.connect(db, timeout=5)
    try:
        with con:
            rows = con.execute(
                "SELECT created_at_ms, payload_json FROM queued_items WHERE thread_id = ? ORDER BY queue_order",
                (codex_thread_id,),
            ).fetchall()
            if rows:
                con.execute("DELETE FROM queued_items WHERE thread_id = ?", (codex_thread_id,))
                # A new revision is how a running Codex notices its queue changed under it.
                con.execute("INSERT OR REPLACE INTO queued_thread_revisions(thread_id) VALUES (?)", (codex_thread_id,))
    finally:
        con.close()
    return [(ms / 1000.0, _item_text(p)) for ms, p in rows]


def save_purged(label: str, items: List[Tuple[float, str]]) -> str:
    """Write purged messages to ~/.agent-peer/purged/ and return the path."""
    d = os.path.join(os.path.expanduser("~"), ".agent-peer", "purged")
    os.makedirs(d, mode=0o700, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", label)
    path = os.path.join(d, f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}.md")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
        for ts, text in items:
            f.write(f"## {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}\n{text}\n\n")
    return path
