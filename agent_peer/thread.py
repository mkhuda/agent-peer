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
    get_thread_push_state_path,
    atomic_write_json,
    ensure_dirs,
)
from .sender import send_message
from .registry import get_active_sessions
from .codex_queue import queue_state
from . import compat

# Total wall-time budget for one fanout across all concurrent workers.
# Real bug, caught live: 0.2s was tuned for a fast socket write (a native
# Claude Code push), but a codex-queue delivery spawns a whole separate
# `codex` process and measured ~1.3s even on a healthy, successful call -
# well past 0.2s, so the poster's own daemon thread got killed mid-flight
# before delivery completed, silently dropping the push. 3s gives that
# room while still bounding a poster's worst-case delay to a few seconds
# rather than hanging indefinitely on a genuinely dead/hung target.
# A re-arm loop refreshes presence within seconds; past this an active member with a dead pid is deaf.
ACTIVE_DEAD_GRACE_SECONDS = 30.0
FANOUT_BUDGET_SECONDS = 3.0
# Only a cap, not a fixed wait: the poster leaves as soon as its workers
# finish. A `codex queue` is a separate process that must start and connect to
# Codex's app-server; on a loaded machine that measured up to ~2.9s before the
# request even arrived (server-side handling is tens of ms), so at 3s the poster
# exited first and the successful delivery never reached the inbox log. Codex
# targets get more headroom; native pushes keep the tight bound.
FANOUT_CODEX_BUDGET_SECONDS = 6.0

# Codex works through queued prompts one turn at a time, so N pushes cost N
# turns: a follow-all Codex in a busy room piles up a queue that drains far
# slower than the room talks, and every late item is already stale. Cutting
# the NUMBER of queued items is what fixes that; shortening text alone would
# not. Applies only to Codex targets - Claude's native push is unchanged.
CODEX_PUSH_MAX_CHARS = 500
CODEX_PUSH_WINDOW_SECONDS = 60.0
# A time window alone cannot stop the pile-up: while Codex sits in one long
# tool call it consumes nothing, so every elapsed window still adds an item.
# Backpressure has to follow consumption, which only Codex's own queue knows
# (read-only, fail-open). Plain follow-all posts wait for an empty queue; a
# mention may queue up to this depth; [stop] is never held.
CODEX_QUEUE_MAX_MENTION = 2


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


_FENCE_LINE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_BACKTICK_RUN_RE = re.compile(r"`+")


def _strip_fenced_code_blocks(content: str) -> str:
    """CommonMark fenced code block rule: an opening fence line (a run of
    >=3 of the same character) is closed by a LATER line that is only a
    same-character run of >=N of that character - not by that substring
    appearing anywhere, including mid-line inside the block's own content
    (e.g. a code block whose content itself mentions ``` as an example)."""
    lines = content.split("\n")
    out = []
    i = 0
    while i < len(lines):
        m = _FENCE_LINE_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        char, n = m.group(1)[0], len(m.group(1))
        close_idx = next(
            (
                j for j in range(i + 1, len(lines))
                if re.fullmatch(rf"[ \t]*{re.escape(char)}{{{n},}}[ \t]*", lines[j])
            ),
            None,
        )
        if close_idx is None:
            # No valid closer anywhere: per CommonMark, an unclosed fence
            # extends to end-of-message - everything after the opener,
            # mentions included, is inside the (malformed) code block.
            # Deliberately conservative here: a missed mention is
            # recoverable (resend it plainly), an accidental summon is not.
            break
        i = close_idx + 1
    return "\n".join(out)


def _strip_inline_code_spans(content: str) -> str:
    """CommonMark inline code span rule: an opening run of N backticks is
    closed by the NEXT run of exactly N backticks (not more, not fewer) -
    unlike fenced blocks, a regex backreference can't express this alone,
    because a shorter/longer run elsewhere would wrongly pair with it (a
    literal ``` typed inside a single-backtick span, for example)."""
    runs = list(_BACKTICK_RUN_RE.finditer(content))
    out = []
    pos = 0
    i = 0
    while i < len(runs):
        opener = runs[i]
        close_idx = next(
            (j for j in range(i + 1, len(runs)) if len(runs[j].group()) == len(opener.group())),
            None,
        )
        if close_idx is None:
            i += 1
            continue
        out.append(content[pos:opener.start()])
        pos = runs[close_idx].end()
        i = close_idx + 1
    out.append(content[pos:])
    return " ".join(out)


def _strip_code_spans(content: str) -> str:
    """Drop fenced and inline code spans before mention extraction - a name
    quoted as a literal code example ("the socket at `/tmp/.../@name.sock`")
    must not be read as an @mention."""
    content = _strip_fenced_code_blocks(content)
    return _strip_inline_code_spans(content)


def _mention_tokens(content: str):
    # Token-exact (not substring): "@codex-8763" must not match a session
    # literally named "codex-8763-2" in another project.
    return set(re.findall(r"@([A-Za-z0-9_.\-]+)", _strip_code_spans(content)))


def _mentions(content: str, name: str) -> bool:
    tokens = _mention_tokens(content)
    # [stop] must go through the same code-span stripping as @tokens - a
    # doc example quoting the "[stop]" prefix convention must not itself
    # trigger a real stop-knock.
    return name in tokens or "all" in tokens or "[stop]" in _strip_code_spans(content)


def fanout_targets(thread_id: str, sender: str, content: str) -> List[Tuple[str, int]]:
    """Who gets a socket push for this post. Active members (left=False)
    never do - they read the room stream via their own poll, and the
    socket is the out-of-room intercom, never a second room speaker.
    Three paths knock for a gated (left=True) member: an explicit mention
    (left untouched - knock is a doorbell, not an enrollment), a standing
    `follow_all` opt-in (0035 - every other participant's post knocks, not
    just mentions - exact sender-identity comparison, never a substring
    match), and a mesh session with no presence entry at all explicitly
    mentioned (one-shot, never enrolled - knock is not an invite; scoped to
    the sender's own workspace only - a plain @name must not page an
    unrelated session in a different project). Mentions inside inline or
    fenced code spans are never read as @mentions at all, in any path."""
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
            # An active member whose poll process has long ended is deaf; a mention knock is its only way in.
            last_seen = info.get("last_seen")
            last_seen = last_seen if isinstance(last_seen, (int, float)) else 0
            if (compat.is_pid_alive(pid) or time.time() - last_seen <= ACTIVE_DEAD_GRACE_SECONDS
                    or not _mentions(content, name)):
                continue
            targets.append((name, pid))
            continue
        # A still-running peek already sees the mention via its own poll.
        if compat.is_pid_alive(pid):
            continue
        if not info.get("follow_all") and not _mentions(content, name):
            continue
        targets.append((name, pid))
    pushed = {name for name, _ in targets}
    sender_cwd = os.path.normpath(os.getcwd())
    for token in _mention_tokens(content):
        # Same-workspace summons only: exact name, no presence entry (the
        # presence loop above already decided everyone inside the room), no
        # enroll. Scoped to the sender's own cwd - a plain @name in chat must
        # not page an unrelated session in a different project just because
        # it happens to share a name (confirmed live: a narrative mention of
        # another project's session name pushed the full message to it).
        # Cross-project summons are a deliberate, separate action (the
        # interactive invite picker, `join --all`), not implied by @name.
        if token == sender or token in pushed or token in presence:
            continue
        session = next((s for s in sessions if s.get("name") == token), None)
        if session is None:
            continue
        session_cwd = session.get("cwd")
        if not session_cwd or os.path.normpath(os.path.expanduser(session_cwd)) != sender_cwd:
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


def _codex_sessions() -> Dict[str, Optional[str]]:
    """Live Codex session name -> its native queue thread id (may be None)."""
    try:
        return {
            s.get("name"): s.get("codexThreadId")
            for s in get_active_sessions()
            if str(s.get("agentType") or "").upper() == "CODEX" and s.get("name")
        }
    except Exception:
        return {}


def _codex_pending_count(codex_thread_id: Optional[str]) -> Optional[int]:
    """Items still waiting in this Codex session's native queue, or None when
    it cannot be read (see codex_queue.queue_state)."""
    state = queue_state(codex_thread_id)
    return None if state is None else state[0]


def _codex_preview(content: str, thread_id: str) -> str:
    if len(content) <= CODEX_PUSH_MAX_CHARS:
        return content
    return (
        content[:CODEX_PUSH_MAX_CHARS].rstrip()
        + f"... [truncated, {len(content)} chars - full text: agent-peer logs --thread {thread_id} -n 10]"
    )


def _registered_names() -> Optional[set]:
    """Names of every registered agent session, or None when unknown."""
    try:
        return {s.get("name") for s in get_active_sessions() if s.get("name")}
    except Exception:
        return None


def _codex_push_decision(
    thread_id: str, name: str, seq: int, content: str, codex_thread_id: Optional[str] = None,
    human: bool = False,
) -> Tuple[bool, str]:
    """Two brakes per (thread, Codex target). Depth: while Codex's own queue
    already holds an unconsumed item, further plain posts are held (Codex is
    guaranteed to run again, and its next peek sees everything still unread);
    a mention may queue up to CODEX_QUEUE_MAX_MENTION. Time: a plain post is
    also held inside CODEX_PUSH_WINDOW_SECONDS of the last push. Held posts
    are counted, and the next push that goes out carries a "+N not pushed"
    note. The poster process exits right after posting, so no timer is left
    to flush a trailing push - a held message stays in the thread and counts
    as unread for the participant's cursor. [stop] and any post from a human
    (a sender that is not a registered agent session) are never held: the
    brakes exist for agent-to-agent traffic, and a person's instruction must
    not wait behind it. If the queue depth is unreadable only the time window
    applies, and any failure here fails open (push as before) and never
    raises into the poster."""
    try:
        now = time.time()
        path = get_thread_push_state_path(thread_id, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
        last = float(state.get("last_push_ts", 0) or 0)
        pending = int(state.get("suppressed", 0) or 0)
        first = int(state.get("first_suppressed_seq", 0) or 0)
        stop = "[stop]" in _strip_code_spans(content)
        mention = _mentions(content, name)
        hold = False
        if not stop and not human:
            queued = _codex_pending_count(codex_thread_id)
            if queued is not None and queued >= (CODEX_QUEUE_MAX_MENTION if mention else 1):
                hold = True
            elif not mention and now - last < CODEX_PUSH_WINDOW_SECONDS:
                hold = True
        if hold:
            atomic_write_json(path, {
                "last_push_ts": last,
                "suppressed": pending + 1,
                "first_suppressed_seq": first or seq,
            })
            return False, ""
        note = ""
        if pending:
            note = (
                f"(+{pending} earlier message(s) since #{first} were not pushed - "
                f"agent-peer logs --thread {thread_id} -n {pending + 2})"
            )
        atomic_write_json(path, {"last_push_ts": now, "suppressed": 0, "first_suppressed_seq": 0})
        return True, note
    except Exception:
        return True, ""


def fanout_thread_push(thread_id: str, seq: int, sender: str, content: str):
    """Best-effort native wake for active participants. Daemon workers plus
    a bounded main-thread wait: a hung socket can delay a post by at most
    FANOUT_BUDGET_SECONDS and can never wedge interpreter exit. Never raises."""
    try:
        targets = fanout_targets(thread_id, sender, content)
        if not targets:
            return
        codex_sessions = _codex_sessions()
        registered = _registered_names()
        human = registered is not None and sender not in registered
        workers = []
        codex_workers = set()
        for name, pid in targets:
            body, note = content, ""
            if name in codex_sessions:
                push, note = _codex_push_decision(thread_id, name, seq, content, codex_sessions[name], human)
                if not push:
                    continue
                body = _codex_preview(content, thread_id)
                # A queued frame can run many minutes after it was sent.
                note = (note + "\n" if note else "") + (
                    f"(Sent {time.strftime('%H:%M:%S')}; it may have waited in the queue. Before acting, run "
                    f"agent-peer thread {thread_id} --timeout 10 - a newer [change]/[stop] there overrides this.)"
                )
            frame = (
                f"[thread: {thread_id} #{seq} from {sender} · {time.strftime('%H:%M:%S')}]: {body}\n"
                + (f"{note}\n" if note else "")
                + f'(Reply in this thread: agent-peer send --thread {thread_id} "...")'
            )
            worker = threading.Thread(target=_push_one, args=(name, pid, frame, sender), daemon=True)
            worker.start()
            workers.append(worker)
            if name in codex_sessions:
                codex_workers.add(worker)
        started = time.time()
        for worker in workers:
            limit = FANOUT_CODEX_BUDGET_SECONDS if worker in codex_workers else FANOUT_BUDGET_SECONDS
            remaining = started + limit - time.time()
            if remaining <= 0:
                continue
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


def touch_thread_presence(thread_id: str, participant: str, left: bool = False, follow: Optional[bool] = None):
    """Records that `participant` is waiting right now, so a viewer can
    tell "who's listening" from "who has ever posted". `left` marks a peek
    (timeout-bounded): visible in the room, but gated from banter push.
    `follow` (0035) is a standing opt-in, not a per-call state like `left` -
    pass `True` to turn it on, leave as `None` to carry forward whatever it
    was already set to (so a plain re-peek doesn't silently drop it), or
    `False` to explicitly clear it without a full `--leave`.
    Emits one system join event on genuine activation only (0031)."""
    path = get_thread_presence_path(thread_id)
    lock_path = get_thread_lock_path(thread_id) + ".presence"
    try:
        lock_handle = _acquire_thread_lock(lock_path, timeout=0.5)
    except OSError:
        return
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
        follow_all = follow if follow is not None else bool(isinstance(prior, dict) and prior.get("follow_all"))
        presence[participant] = {
            "pid": os.getpid(),
            "last_seen": time.time(),
            "left": left,
            "follow_all": follow_all,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(presence, f)
        _secure(path)
    except OSError:
        return
    finally:
        compat.release_lock(lock_handle)
    if emit_join:
        _append_system_event(thread_id, "join", participant)


def leave_thread_presence(thread_id: str, participant: str) -> bool:
    """Step out of the meeting (soft-leave): mark the presence entry left
    so banter skips this participant, while @mention/@all/[stop] can still
    knock (Phase 6). Also clears any standing `follow_all` opt-in (0035) -
    `--leave` is the one documented way to fully reset, so it must not
    leave a stale follow-all behind for the next `--leave`-less rejoin to
    silently inherit. The cursor is deliberately left alone - rejoining
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
        entry["follow_all"] = False
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


def split_for_mention_only(unread: List[Dict[str, Any]], participant: str) -> Tuple[List[Dict[str, Any]], int, int]:
    """(posts that mention the participant, how many other real posts are left out,
    how many records the thread's unread range spans - the `logs -n` value that shows it all)."""
    shown = [m for m in unread if _mentions(m.get("content") or "", participant)]
    hidden = sum(1 for m in unread if m not in shown and m.get("from") != "system")
    span = unread[-1]["seq"] - unread[0]["seq"] + 1 if unread else 0
    return shown, hidden, span


def wait_for_thread_message(
    thread_id: str,
    participant: str,
    timeout: Optional[float] = None,
    follow: Optional[bool] = None,
    mention_only: bool = False,
) -> Optional[List[Dict[str, Any]]]:
    """Immediate-backlog-then-poll, like inbox.py's wait_for_message but
    shared. Cursor advances to unread[-1]'s seq (not a fresh re-read, which
    could race a concurrent append and skip it before it's ever returned).
    Timeout-as-intent: a bounded wait is a peek (gated), only an indefinite
    wait arms full room presence. `follow` (0035) opts a gated participant
    into "follow-all" - every other participant's post knocks, not just
    @mentions - see touch_thread_presence for its carry-forward semantics.
    `mention_only` stays silent (cursor untouched) until a post mentions the
    participant, then returns every unread post as context."""
    touch_thread_presence(thread_id, participant, left=(timeout is not None), follow=follow)

    def ready(unread):
        return bool(unread) and (not mention_only or any(_mentions(m.get("content") or "", participant) for m in unread))

    unread = get_thread_unread(thread_id, participant)
    if ready(unread):
        _write_thread_cursor(thread_id, participant, unread[-1]["seq"])
        return unread

    t0 = time.time()
    while True:
        unread = get_thread_unread(thread_id, participant)
        if ready(unread):
            _write_thread_cursor(thread_id, participant, unread[-1]["seq"])
            return unread
        if timeout is not None and (time.time() - t0) >= timeout:
            return None
        time.sleep(0.1)
