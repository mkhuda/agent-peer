"""Interactive foreman-facing thread session - agents keep using the plain
`agent-peer thread <id>` (thread.py's wait_for_thread_message, headless/
scriptable); this is the human two-way live view + invite flow on top of it."""

import os
import sys
import threading

from .protocol import get_thread_path
from .registry import get_active_sessions
from .sender import send_message
from .thread import read_thread, append_thread_message, touch_thread_presence, leave_thread_presence, read_thread_presence
from .logs import format_thread_entry, format_thread_presence_header, supports_color, get_session_cache
from .picker import pick_multi
from .rawline import LineEditor, read_message


def _thread_is_new(thread_id: str) -> bool:
    path = get_thread_path(thread_id)
    return not os.path.exists(path) or os.path.getsize(path) == 0


def _candidate_sessions(all_scope: bool):
    sessions = [s for s in get_active_sessions() if s.get("alive")]
    if all_scope:
        return sessions
    cwd = os.path.normpath(os.getcwd())
    return [
        s for s in sessions
        if s.get("cwd") and os.path.normpath(os.path.expanduser(s["cwd"])) == cwd
    ]


def _mention_candidates(thread_id: str, participant: str) -> list:
    """Tab-completion pool for @mention: this thread's own participants
    (they're relevant regardless of cwd - they already joined) union any
    ALIVE session in the same workspace (same cwd() scoping _candidate_
    sessions already uses for the invite picker, and fanout_targets' own
    mesh-wide-summons scoping) - not the whole mesh."""
    names = {n for n in read_thread_presence(thread_id).keys() if n != participant}
    names |= {s.get("name") for s in _candidate_sessions(all_scope=False) if s.get("name") != participant}
    return sorted(names)


def _render_session(s):
    return f"{s.get('name')}  ({s.get('agentType', '?')}, {s.get('status', 'idle')}, {s.get('cwd', '?')})"


def _invite_message(thread_id: str, agent_type: str) -> str:
    """Harness-safe invite text: an indefinite `thread <id>` is only safe
    advice for a harness with a persistent poll loop (Claude/Muse). Codex
    has no such loop (confirmed live: an indefinite call can die between
    its own runtime's turn cuts with nothing re-arming it), so it gets
    pointed at its own skill + a bounded peek instead of a command that
    would strand it deaf. Unknown/other harnesses get a generic pointer.
    Normalizes case: registry.py's get_session_agent_type() returns "AGY"/
    "CODEX" uppercase but its own Claude Code fallback returns "Claude"
    title-case - a real bug caught live, an invite sent to a native Claude
    session fell through to the generic branch instead of matching here."""
    agent_type = (agent_type or "").upper()
    if agent_type == "CODEX":
        return (
            f"[change]: Foreman invited you to thread '{thread_id}'. "
            f"Please check your skill instructions first, then join via "
            f"bounded peek & poll ('agent-peer thread {thread_id} --timeout 10')."
        )
    if agent_type == "AGY":
        return (
            f"[change]: Foreman invited you to thread '{thread_id}'. "
            f"Check thread '{thread_id}' per your skill workflow."
        )
    if agent_type in ("CLAUDE", "MUSE"):
        return (
            f"[change]: Foreman invited you to thread '{thread_id}'. "
            f"Join with: 'agent-peer thread {thread_id}'."
        )
    return (
        f"[change]: Foreman invited you to thread '{thread_id}'. "
        f"Please check thread '{thread_id}' per your agent harness idiom."
    )


def invite_agents(thread_id: str, sender: str, all_scope: bool = False) -> list:
    """Shows the picker, DMs each selected session to go join. Returns the
    names actually invited (empty on no uninvited candidates, cancel, or no
    TTY). Excludes sessions ALREADY active in the room - not soft-left ones,
    which remain legitimately re-invitable (e.g. Codex's own bounded-peek
    idiom always leaves it `left: true` between checks)."""
    presence = read_thread_presence(thread_id)
    active_in_room = {
        name for name, info in presence.items()
        if isinstance(info, dict) and not info.get("left", False)
    }
    sessions = [
        s for s in _candidate_sessions(all_scope)
        if s.get("name") != sender and s.get("name") not in active_in_room
    ]
    if not sessions:
        return []
    title = f"Invite agents to '{thread_id}'" + ("" if all_scope else f" (workspace: {os.getcwd()})")
    try:
        selected_ids = pick_multi(sessions, title, render_item_fn=_render_session, get_id_fn=lambda s: s.get("name"))
    except RuntimeError as e:
        print(f"Could not open the picker ({e}) - skipping invites.")
        return []

    invited = []
    for s in sessions:
        name = s.get("name")
        if name not in selected_ids:
            continue
        try:
            send_message(
                target=name,
                content=_invite_message(thread_id, s.get("agentType", "")),
                priority="now",
                from_name=sender,
            )
            invited.append(name)
        except Exception as e:
            print(f"Could not invite {name}: {e}", file=sys.stderr)
    return invited


def _print_live(text: str, editor):
    """Clears the in-progress input line, prints the incoming message, then
    redraws the prompt + whatever the foreman had already typed (editor is
    None in the non-tty fallback path - nothing to redraw there)."""
    sys.stdout.write("\r\033[K")
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    if editor is not None:
        editor.render()
    sys.stdout.flush()


def _next_entry(r, use_color, viewer, session_cache, last_sender_box):
    """Renders one record and advances the shared last_sender_box - a tiny
    wrapper so the three separate print sites in run_join (backlog, live
    poll, and your own sent message) all group consecutive same-sender
    entries against ONE shared history instead of three isolated ones."""
    text = format_thread_entry(
        r, use_color=use_color, viewer=viewer,
        session_cache=session_cache, last_sender=last_sender_box[0],
    )
    if r.get("from") != "system" and r.get("type") != "event":
        last_sender_box[0] = r.get("from")
    return text


def _poll_loop(thread_id, participant, last_seq_box, stop_event, use_color, editor, session_cache, last_sender_box, paused=None):
    while not stop_event.is_set():
        try:
            if paused is not None and paused.is_set():
                # /invite has curses on screen - printing here would corrupt it.
                stop_event.wait(0.2)
                continue
            for r in read_thread(thread_id):
                seq = r.get("seq", 0)
                if seq <= last_seq_box[0]:
                    continue
                last_seq_box[0] = seq
                if r.get("from") == participant:
                    continue  # already visible from your own typed line
                _print_live(_next_entry(r, use_color, participant, session_cache, last_sender_box), editor)
            touch_thread_presence(thread_id, participant, left=False)
            if editor is not None:
                # Keeps @mention Tab-completion candidates live as people
                # join/leave, without the compose box's blocking read loop
                # needing to poll for it itself.
                editor.mention_candidates = _mention_candidates(thread_id, participant)
        except Exception as e:
            print(f"agent-peer: poll tick skipped ({e})", file=sys.stderr)
        stop_event.wait(0.5)


def run_join(thread_id: str, participant: str, invite: bool = False, all_scope: bool = False):
    """The one-door entry: invite (new thread, or --invite) then the live
    two-way view. A thread has no create/teardown step, so re-running this
    later against the same id is simply the rejoin case."""
    use_color = supports_color()
    presence = read_thread_presence(thread_id)
    room_is_empty = not any(
        name != participant and isinstance(info, dict) and not info.get("left", False)
        for name, info in presence.items()
    )
    if invite or _thread_is_new(thread_id) or room_is_empty:
        invited = invite_agents(thread_id, participant, all_scope=all_scope)
        if invited:
            print(f"Invited: {', '.join(invited)}")

    session_cache = get_session_cache()
    last_sender_box = [None]

    backlog = read_thread(thread_id)
    print(f"\n=== THREAD '{thread_id}' ===")
    header = format_thread_presence_header(thread_id, use_color)
    if header:
        print(header)
    print("Type a message and press Enter to send. Tab completes @mentions. Ctrl+D to leave.\n")
    for r in backlog:
        # unlike _poll_loop, backlog is full history - self-echo suppression
        # there only hides what your own live typing already echoed locally,
        # a rejoin has no such echo and must show everything
        print(_next_entry(r, use_color, participant, session_cache, last_sender_box))

    # A real tty gets the raw/cbreak multi-line editor (Esc clears the whole
    # composition, Shift+Enter/Alt+Enter/trailing "\" all continue composing
    # instead of submitting); a non-tty caller (this module's own piped-
    # stdin tests) keeps plain input() - rawline's raw-mode read assumes a
    # real terminal and would fail against a pipe.
    use_raw = sys.stdin.isatty()
    editor = LineEditor("> ") if use_raw else None
    if editor is not None:
        editor.mention_candidates = _mention_candidates(thread_id, participant)

    last_seq_box = [backlog[-1]["seq"] if backlog else 0]
    stop_event = threading.Event()
    invite_paused = threading.Event()
    touch_thread_presence(thread_id, participant, left=False)
    poller = threading.Thread(
        target=_poll_loop,
        args=(thread_id, participant, last_seq_box, stop_event, use_color, editor, session_cache, last_sender_box, invite_paused),
        daemon=True,
    )
    poller.start()

    try:
        while True:
            if use_raw:
                text = read_message(editor)
            else:
                try:
                    text = input("> ")
                except (EOFError, KeyboardInterrupt):
                    text = None
            if text is None:
                break
            text = text.strip()
            if not text:
                continue
            if text == "/invite" or text.startswith("/invite "):
                # Pause the poller before curses takes the screen - anything
                # it prints mid-picker would corrupt the picker's rendering.
                invite_paused.set()
                try:
                    invited = invite_agents(thread_id, participant, all_scope="--all" in text.split())
                finally:
                    invite_paused.clear()
                print(f"Invited: {', '.join(invited)}" if invited else "No new agents to invite.")
                if editor is not None:
                    editor.render()
                continue
            record = append_thread_message(thread_id, participant, text)
            last_seq_box[0] = max(last_seq_box[0], record["seq"])
            if editor is not None:
                editor.clear_rendered_area()
            print(_next_entry(record, use_color, participant, session_cache, last_sender_box))
    finally:
        stop_event.set()
        poller.join(timeout=2)
        # A real departure, not just a stopped screen: mark left (emits
        # the system leave event) so the notice below is true.
        leave_thread_presence(thread_id, participant)
        print(f"\nLeft the thread. To rejoin, run: agent-peer join {thread_id}")
