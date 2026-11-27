"""Holds messages for a busy native Claude session and delivers them, batched, once it is free.

Each queued message is its own file under ~/.agent-peer/outbox/<pid>/, so senders never share a file.
One short-lived drainer per target (guarded by a lock file) delivers them; the peer token is never stored."""

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Dict, List, Optional, Tuple

from . import compat
from .inbox import append_inbox
from .protocol import AGENT_PEER_DIR, SESSIONS_DIR, is_pid_alive, read_text_retry

OUTBOX_DIR = os.path.join(AGENT_PEER_DIR, "outbox")
RANK = {"now": 0, "next": 1, "later": 2}
MAX_BATCH_CHARS = 16000
DEDUPE_S = 30.0
MAX_DELIVERY_TRIES = 3
MAX_LOOP_ERRORS = 20


def _cfg(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _dir(pid) -> str:
    return os.path.join(OUTBOX_DIR, str(pid))


def _inflight_dir(pid) -> str:
    return os.path.join(_dir(pid), "inflight")


def _lock_path(pid) -> str:
    return os.path.join(OUTBOX_DIR, f"{pid}.lock")


def _flush_flag(pid) -> str:
    return os.path.join(OUTBOX_DIR, f"{pid}.flush")


def _ensure(pid) -> None:
    for d in (OUTBOX_DIR, _dir(pid), _inflight_dir(pid)):
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            compat.secure_dir(d)


def bypasses(content: str) -> bool:
    """A [stop] message exists to interrupt, so it is never held back."""
    return content.lstrip().lower().startswith("[stop]")


def is_busy(session: Dict) -> bool:
    return str(session.get("status") or "") == "busy"


def _entries(pid) -> List[Tuple[str, Dict]]:
    """Waiting messages, highest priority first and oldest first within a priority."""
    d = _dir(pid)
    try:
        names = os.listdir(d)
    except OSError:
        return []
    out = []
    for n in names:
        if not n.endswith(".json") or n.startswith("."):
            continue
        path = os.path.join(d, n)
        try:
            with open(path, "r", encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            out.append((path, rec))
    out.sort(key=lambda pr: (RANK.get(pr[1].get("priority"), 0), os.path.basename(pr[0])))
    return out


def _write_entry(pid, rec: Dict) -> str:
    fname = f"{time.time_ns():020d}-{rec['id']}.json"
    final = os.path.join(_dir(pid), fname)
    tmp = os.path.join(_dir(pid), f".{fname}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f)
    os.replace(tmp, final)
    return final


def enqueue(session: Dict, content: str, from_name: str, from_cwd: Optional[str], priority: str) -> Dict:
    """Hold one message for `session`; returns {"id", "position", "deduped"}."""
    pid = session["pid"]
    name = session.get("name") or str(pid)
    _ensure(pid)
    entries = _entries(pid)
    now = time.time()
    for i, (_, e) in enumerate(entries):
        if e.get("from") == from_name and e.get("content") == content and now - e.get("enqueued_at", 0) < DEDUPE_S:
            return {"id": e["id"], "position": i + 1, "deduped": True}
    limit = int(_cfg("AGENT_PEER_OUTBOX_MAX", 50))
    if len(entries) >= limit:
        raise RuntimeError(
            f"{name} is busy and already has {len(entries)} messages waiting. Retry once it is free, or add --now to interrupt it."
        )
    rec = {
        "id": uuid.uuid4().hex[:8], "from": from_name, "from_cwd": from_cwd, "sent_at": now, "enqueued_at": now,
        "priority": priority if priority in RANK else "now", "content": content,
        "target": name, "pid": pid, "session_id": session.get("sessionId"),
    }
    path = _write_entry(pid, rec)
    spawn_drainer(pid)
    ordered = [os.path.basename(p) for p, _ in _entries(pid)]
    pos = ordered.index(os.path.basename(path)) + 1 if os.path.basename(path) in ordered else len(ordered)
    return {"id": rec["id"], "position": pos, "deduped": False}


def _holder_alive(pid) -> bool:
    try:
        with open(_lock_path(pid), "r", encoding="utf-8") as f:
            return is_pid_alive(int(f.read().strip()))
    except (OSError, ValueError):
        return False


def spawn_drainer(pid) -> bool:
    """Start a detached drainer unless one already holds the lock (it re-checks the queue before it exits)."""
    if _holder_alive(pid):
        return True
    env = dict(os.environ)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    kwargs = {}
    if compat.IS_WINDOWS:
        kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(
            [sys.executable, "-m", "agent_peer.outbox", "--drain", str(pid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=tempfile.gettempdir(), env=env, close_fds=True, **kwargs,
        )
        return True
    except OSError:
        return False


def _clock(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _span(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def _head(e: Dict, now: float) -> str:
    cwd = f" · {e['from_cwd']}" if e.get("from_cwd") else ""
    return f"[from {e['from']}{cwd} · sent {_clock(e['sent_at'])} · held {_span(now - e['enqueued_at'])} while you were busy]\n{e['content']}"


def _render(entries: List[Dict], now: float) -> str:
    if len(entries) == 1:
        return _head(entries[0], now)
    parts = "\n\n".join(_head(e, now) for e in entries)
    return f"[{len(entries)} messages arrived while you were busy, in order]\n\n{parts}"


def _notify(e: Dict, why: str) -> None:
    """Tell the sender, through its own inbox, that a held message did not reach its target."""
    snippet = " ".join(str(e.get("content", "")).split())[:80]
    try:
        append_inbox({
            "from": "agent-peer", "to": e.get("from"), "priority": "now", "type": "user",
            "content": f"[agent-peer] Your message to {e.get('target')} was not delivered: {why}\n> {snippet}",
            "raw": {"transport": "outbox-notice"},
        }, session_name=e.get("from"))
    except Exception:
        pass


def _discard(path: str, rec: Dict, why: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        return
    _notify(rec, why)


def _state(pid, session_id) -> Tuple[str, str]:
    """('gone', reason) | ('busy', '') | ('free', '') from the target's own registry file."""
    try:
        data = json.loads(read_text_retry(os.path.join(SESSIONS_DIR, f"{pid}.json")))
    except FileNotFoundError:
        return "gone", "its session ended"
    except (OSError, ValueError):
        return "busy", ""  # locked or caught mid-write: look again
    if not is_pid_alive(pid):
        return "gone", "its session ended"
    if session_id and data.get("sessionId") and data["sessionId"] != session_id:
        return "gone", "that session was replaced by a new one"
    return ("busy", "") if str(data.get("status") or "") == "busy" else ("free", "")


def _settle_inflight(pid) -> None:
    """A drainer that died mid-delivery leaves entries whose fate is unknown; never resend them."""
    d = _inflight_dir(pid)
    try:
        names = os.listdir(d)
    except OSError:
        return
    for n in names:
        path = os.path.join(d, n)
        try:
            with open(path, "r", encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            rec = None
        try:
            os.unlink(path)
        except OSError:
            continue
        if rec:
            _notify(rec, "delivery was interrupted and it may or may not have arrived; check with the target before resending")


def _take(entries: List[Tuple[str, Dict]]) -> List[Tuple[str, Dict]]:
    batch, size = [], 0
    for path, rec in entries:
        size += len(rec.get("content", "")) + 200
        if batch and size > MAX_BATCH_CHARS:
            break
        batch.append((path, rec))
    return batch


def _claim(pid, batch: List[Tuple[str, Dict]]) -> List[Tuple[str, Dict, str]]:
    """Move entries to inflight before sending, so a crash can never lead to a second delivery."""
    claimed = []
    for path, rec in batch:
        dest = os.path.join(_inflight_dir(pid), os.path.basename(path))
        try:
            os.rename(path, dest)
        except OSError:
            continue  # cancelled meanwhile
        claimed.append((path, rec, dest))
    return claimed


def _deliver(pid, claimed: List[Tuple[str, Dict, str]]) -> None:
    from .registry import resolve_session
    from .sender import write_frames

    session, sock_path, token = resolve_session(str(pid))
    entries = [rec for _, rec, _ in claimed]
    now = time.time()
    senders = {e["from"] for e in entries}
    best = min((e.get("priority", "now") for e in entries), key=lambda p: RANK.get(p, 0))
    write_frames(sock_path, token, _render(entries, now), next(iter(senders)) if len(senders) == 1 else "agent-peer",
                 None, best, session, 5.0)
    name = session.get("name")
    for e in entries:
        try:
            append_inbox({
                "from": e["from"], "from_cwd": e.get("from_cwd"), "to": name, "to_pid": pid,
                "recipient_name": name, "recipient_pid": pid, "priority": e.get("priority", "now"), "type": "user",
                "content": _head(e, now), "raw": {"transport": "uds-native-claude", "held_s": round(now - e["enqueued_at"], 1)},
            }, session_name=name, session_pid=pid)
        except Exception:
            pass


def _serve(pid) -> None:
    poll, debounce, ttl = _cfg("AGENT_PEER_OUTBOX_POLL", 0.5), _cfg("AGENT_PEER_OUTBOX_DEBOUNCE", 1.0), _cfg("AGENT_PEER_OUTBOX_TTL", 6 * 3600)
    _settle_inflight(pid)
    free_since: Optional[float] = None
    failures = errors = 0
    try:
        while True:
            try:
                entries = _entries(pid)
                if not entries:
                    return
                now = time.time()
                live = []
                for path, rec in entries:
                    if now - rec.get("enqueued_at", now) > ttl:
                        _discard(path, rec, f"{rec.get('target')} stayed busy for more than {_span(ttl)}")
                    else:
                        live.append((path, rec))
                if not live:
                    continue
                state, why = _state(pid, live[0][1].get("session_id"))
                if state == "gone":
                    for path, rec in live:
                        _discard(path, rec, why)
                    continue
                forced = os.path.exists(_flush_flag(pid))
                if state == "busy" and not forced:
                    free_since = None
                    time.sleep(poll)
                    continue
                if free_since is None:
                    free_since = time.monotonic()
                if not forced and time.monotonic() - free_since < debounce:
                    time.sleep(min(poll, 0.1))
                    continue
                claimed = _claim(pid, _take(live))
                if not claimed:
                    time.sleep(0.05)
                    continue
                try:
                    _deliver(pid, claimed)
                except Exception as exc:
                    for path, _, dest in claimed:
                        try:
                            os.rename(dest, path)
                        except OSError:
                            pass
                    failures += 1
                    if failures >= MAX_DELIVERY_TRIES:
                        for path, rec in [(p, r) for p, r, _ in claimed]:
                            _discard(path, rec, f"the session did not accept it ({exc})")
                        failures = 0
                    time.sleep(max(poll, 0.2))
                    continue
                failures = 0
                for _, _, dest in claimed:
                    try:
                        os.unlink(dest)
                    except OSError:
                        pass
                free_since = None  # the target turns busy again; wait for the next free moment
                errors = 0
            except Exception:
                errors += 1
                if errors >= MAX_LOOP_ERRORS:
                    return
                time.sleep(poll)
    finally:
        try:
            os.unlink(_flush_flag(pid))
        except OSError:
            pass


def drain(pid) -> None:
    """Deliver everything held for `pid` as it becomes free; returns once the queue is empty."""
    _ensure(pid)
    while True:
        lock = compat.acquire_lock(_lock_path(pid))
        if lock is None:
            return
        try:
            _serve(pid)
        finally:
            compat.release_lock(lock)
        if not _entries(pid):
            return


def pending_counts() -> Dict[int, int]:
    try:
        names = os.listdir(OUTBOX_DIR)
    except OSError:
        return {}
    out = {}
    for n in names:
        if n.isdigit() and os.path.isdir(os.path.join(OUTBOX_DIR, n)):
            count = len(_entries(int(n)))
            if count:
                out[int(n)] = count
    return out


def revive() -> None:
    """Restart the drainer of every queue that still holds messages but has none running."""
    for pid in pending_counts():
        if not _holder_alive(pid):
            spawn_drainer(pid)


def describe(pid) -> List[Dict]:
    return [
        {"id": r.get("id"), "from": r.get("from"), "priority": r.get("priority"), "age_s": time.time() - r.get("enqueued_at", time.time()),
         "text": " ".join(str(r.get("content", "")).split())[:70]}
        for _, r in _entries(pid)
    ]


def cancel(pid, entry_id: Optional[str] = None) -> int:
    n = 0
    for path, rec in _entries(pid):
        if entry_id is None or rec.get("id") == entry_id:
            try:
                os.unlink(path)
                n += 1
            except OSError:
                pass
    return n


def request_flush(pid) -> None:
    """Deliver everything now, busy or not."""
    _ensure(pid)
    with open(_flush_flag(pid), "w", encoding="utf-8") as f:
        f.write("1")
    spawn_drainer(pid)


def main(argv: List[str]) -> None:
    if len(argv) == 2 and argv[0] == "--drain" and argv[1].isdigit():
        drain(int(argv[1]))


if __name__ == "__main__":
    main(sys.argv[1:])
