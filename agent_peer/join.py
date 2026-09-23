"""Interactive foreman-facing thread session - agents keep using the plain
`agent-peer thread <id>` (thread.py's wait_for_thread_message, headless/
scriptable); this is the human two-way live view + invite flow on top of it."""

import os
import sys
import threading

from .protocol import get_thread_path
from .registry import get_active_sessions
from .sender import send_message
from .thread import read_thread, append_thread_message, touch_thread_presence
from .logs import format_thread_entry, format_thread_presence_header, supports_color
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


def _render_session(s):
    return f"{s.get('name')}  ({s.get('agentType', '?')}, {s.get('status', 'idle')}, {s.get('cwd', '?')})"


def invite_agents(thread_id: str, sender: str, all_scope: bool = False) -> list:
    """Shows the picker, DMs each selected session to go join. Returns the
    names actually invited (empty on no candidates, cancel, or no TTY)."""
    sessions = _candidate_sessions(all_scope)
    if not sessions:
        scope_note = "the mesh" if all_scope else f"'{os.getcwd()}'"
        print(f"No active sessions found in {scope_note} to invite.")
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
                content=f"[change]: Foreman started thread '{thread_id}' - run `agent-peer thread {thread_id}` to join the discussion.",
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


def _poll_loop(thread_id, participant, last_seq_box, stop_event, use_color, editor):
    while not stop_event.is_set():
        for r in read_thread(thread_id):
            seq = r.get("seq", 0)
            if seq <= last_seq_box[0]:
                continue
            last_seq_box[0] = seq
            if r.get("from") == participant:
                continue  # already visible from your own typed line
            _print_live(format_thread_entry(r, use_color=use_color, viewer=participant), editor)
        touch_thread_presence(thread_id, participant)
        stop_event.wait(0.5)


def run_join(thread_id: str, participant: str, invite: bool = False, all_scope: bool = False):
    """The one-door entry: invite (new thread, or --invite) then the live
    two-way view. A thread has no create/teardown step, so re-running this
    later against the same id is simply the rejoin case."""
    use_color = supports_color()
    if invite or _thread_is_new(thread_id):
        invited = invite_agents(thread_id, participant, all_scope=all_scope)
        if invited:
            print(f"Invited: {', '.join(invited)}")

    backlog = read_thread(thread_id)
    print(f"\n=== THREAD '{thread_id}' ===")
    header = format_thread_presence_header(thread_id, use_color)
    if header:
        print(header)
    print("Type a message and press Enter to send. Ctrl+D to leave.\n")
    for r in backlog:
        # unlike _poll_loop, backlog is full history - self-echo suppression
        # there only hides what your own live typing already echoed locally,
        # a rejoin has no such echo and must show everything
        print(format_thread_entry(r, use_color=use_color, viewer=participant))

    # A real tty gets the raw/cbreak multi-line editor (Esc clears the whole
    # composition, Shift+Enter/Alt+Enter/trailing "\" all continue composing
    # instead of submitting); a non-tty caller (this module's own piped-
    # stdin tests) keeps plain input() - rawline's raw-mode read assumes a
    # real terminal and would fail against a pipe.
    use_raw = sys.stdin.isatty()
    editor = LineEditor("> ") if use_raw else None

    last_seq_box = [backlog[-1]["seq"] if backlog else 0]
    stop_event = threading.Event()
    touch_thread_presence(thread_id, participant)
    poller = threading.Thread(target=_poll_loop, args=(thread_id, participant, last_seq_box, stop_event, use_color, editor), daemon=True)
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
            record = append_thread_message(thread_id, participant, text)
            last_seq_box[0] = max(last_seq_box[0], record["seq"])
    finally:
        stop_event.set()
        poller.join(timeout=2)
        print("\nLeft the thread.")
