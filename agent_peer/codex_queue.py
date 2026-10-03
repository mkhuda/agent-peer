"""Read-only view of a Codex session's native message queue.

Codex offers no CLI for its queue, so this opens the queue database Codex keeps
under ~/.codex read-only. The schema is Codex-internal: any surprise (file
missing or renamed, locked, different columns) returns None and every caller
falls back to not knowing rather than failing."""
import glob
import os
import re
import sqlite3
from typing import Optional, Tuple


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
        dbs = sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".codex", "queue_*.sqlite")))
        if not dbs:
            return None
        con = sqlite3.connect(f"file:{dbs[-1]}?mode=ro", uri=True, timeout=0.3)
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
