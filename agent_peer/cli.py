import sys
import os
import argparse
import json
import time
import fcntl

from .registry import get_active_sessions, resolve_session
from .sender import send_message
from .listener import PeerListener
from .inbox import read_inbox, clear_inbox, wait_for_message, wait_for_reply
from .logs import show_logs
from .agy_status import format_agy_status, get_agy_status_dict
from .claude_status import format_claude_status, get_claude_status_dict
from .codex_status import format_codex_status, get_codex_status_dict
from .protocol import get_lock_path, auto_session_name, detect_harness_identity, get_harness_cwd, SESSIONS_DIR, SOCKET_DIR
from . import agy_live
from . import __version__
from . import harness_detect, setup_tui, update_check

def cmd_list(args):
    sessions = get_active_sessions()

    cwd_filter = getattr(args, "cwd", None)
    if cwd_filter:
        needle = os.path.expanduser(cwd_filter).rstrip("/").lower()
        sessions = [s for s in sessions if needle in (s.get("cwd") or "").lower()]

    if not sessions:
        print("No active Claude Code sessions found." if not cwd_filter else f"No sessions found with cwd matching '{cwd_filter}'.")
        return

    print(f"{'PID':<8} {'SESSION NAME':<24} {'ENGINE':<8} {'STATUS':<8} {'ALIVE':<6} {'SOCKET':<28} {'CWD'}")
    print("-" * 110)
    for s in sessions:
        pid = str(s.get("pid", ""))
        name = s.get("name") or "(untitled)"
        engine = s.get("agentType", "Claude")
        status = s.get("status") or "-"
        alive = "yes" if s.get("alive") else "no"
        sock = os.path.basename(s.get("messagingSocketPath", ""))
        cwd = s.get("cwd", "")
        # Shorten home in cwd
        home = os.path.expanduser("~")
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home):]
        if len(cwd) > 30:
            cwd = "..." + cwd[-27:]
        print(f"{pid:<8} {name:<24} {engine:<8} {status:<8} {alive:<6} {sock:<28} {cwd}")
    print(f"\nTotal: {len(sessions)} sessions registered in ~/.claude/sessions/")

def cmd_prune(args):
    """Remove registrations for sessions whose process is confirmed dead
    (ALIVE: no in 'list') - e.g. a listener killed with SIGKILL never got the
    chance to clean up after itself. Never touches a session still alive."""
    sessions = get_active_sessions()
    dead = [s for s in sessions if not s.get("alive")]
    if not dead:
        print("Nothing to prune - every registered session is alive.")
        return

    # A sandboxed kill(pid, 0) can EPERM for every other process, making a
    # live session look dead - refuse rather than wipe the whole registry.
    if len(dead) == len(sessions) and len(sessions) > 1 and not args.force:
        print(
            f"⚠️  All {len(sessions)} registered sessions show ALIVE: no at once - refusing to "
            "prune.\n"
            "This is more likely a sandboxed environment blocking the liveness check itself "
            "(e.g. kill(pid, 0) returning EPERM for other processes) than every session "
            "genuinely dying simultaneously. Re-run with --force if you're sure they're really "
            "all dead.",
            file=sys.stderr,
        )
        sys.exit(1)

    for s in dead:
        pid = s.get("pid")
        name = s.get("name") or "(untitled)"
        removed = []
        paths = [
            os.path.join(SESSIONS_DIR, f"{pid}.json"),
            s.get("keyFile"),
            s.get("messagingSocketPath"),
            os.path.join(SOCKET_DIR, f"{name}.sock"),
        ]
        for p in paths:
            if not p:
                continue
            if os.path.exists(p) or os.path.islink(p):
                try:
                    os.unlink(p)
                    removed.append(p)
                except OSError:
                    pass
        print(f"🧹 Pruned '{name}' (PID {pid}, dead) - removed {len(removed)} file(s)")

    print(f"\nPruned {len(dead)} dead session(s).")

def cmd_send(args):
    sender = args.sender or os.environ.get("AGENT_PEER_NAME") or auto_session_name()
    # Captured before delivery so a fast reply can never predate the baseline.
    sent_at = time.time()
    try:
        res = send_message(
            target=args.target,
            content=args.message,
            priority=args.priority,
            from_name=sender
        )
        print(f"✅ Delivered in {res['elapsed_ms']}ms to {res['target_name']} (PID {res['target_pid']})")
        print(f"   Socket:   {res['target_socket']}")
        print(f"   Priority: {res['priority']}")
        print(f"   From:     {res['from']}")
        print(f"   Message:  {res['message']}")
    except Exception as e:
        print(f"❌ Failed to send: {e}", file=sys.stderr)
        sys.exit(1)

    if args.await_reply is None:
        return
    # Observational only: watches the sender's own inbox for the target's
    # reply without touching the read cursor (a later `wait` still owns that).
    timeout = args.await_reply if args.await_reply > 0 else None
    reply = wait_for_reply(
        session=sender,
        from_name=res["target_name"],
        after_ts=sent_at,
        timeout=timeout
    )
    if reply:
        print(f"📬 [REPLY from {reply.get('from', 'unknown')}]: {reply.get('content')}")
        sys.exit(0)
    print(f"Timeout waiting for a reply from {res['target_name']}.")
    sys.exit(1)

def cmd_listen(args):
    name = args.name or os.environ.get("AGENT_PEER_NAME") or auto_session_name()
    # Engine is detected independently of the session name, even if --name
    # was given manually, so it always reflects the real calling harness.
    harness, harness_pid = detect_harness_identity()
    codex_thread_id = args.codex_thread or os.environ.get("CODEX_THREAD_ID")
    # Prefer the harness process's own stable cwd over this subprocess's own.
    cwd = args.cwd or (get_harness_cwd(harness_pid) if harness_pid else None)
    listener = PeerListener(
        name=name,
        cwd=cwd,
        agent_type=(harness.upper() if harness else None),
        codex_thread_id=codex_thread_id,
        force=args.force
    )
    listener.run()

def cmd_inbox(args):
    session = args.session or os.environ.get("AGENT_PEER_NAME")
    if args.clear:
        clear_inbox(session=session)
        print(f"Inbox for '{session or 'all'}' cleared.")
        return

    messages = read_inbox(session=session, limit=args.limit)
    if not messages:
        target_label = f" for '{session}'" if session else ""
        print(f"Inbox{target_label} is empty.")
        return

    target_label = f" for '{session}'" if session else ""
    print(f"📬 Recent messages{target_label} ({len(messages)}):\n")
    for m in messages:
        iso = m.get("received_iso", "-")
        sender = m.get("from", "unknown")
        prio = m.get("priority", "normal")
        content = m.get("content", "")
        cwd_suffix = f" (cwd: {m['from_cwd']})" if m.get("from_cwd") else ""
        print(f"[{iso}] From: {sender}{cwd_suffix} (Priority: {prio})")
        print(f"   {content}\n")

def _refuse_if_unreachable(session: str):
    """'wait' only reads the inbox, it never opens a socket itself - if
    nobody ever ran 'listen' for this session, nobody could 'send' to it
    either, so blocking is a guaranteed dead end. Refuse rather than hang
    forever; never auto-starts a listener itself."""
    try:
        resolve_session(session)
    except Exception:
        print(f"❌ No listener running for session '{session}' - nobody can reach you via 'agent-peer send' right now, so 'wait' would block forever for nothing. Run 'agent-peer listen' first.", file=sys.stderr)
        sys.exit(1)

def _reset_status_idle(session: str):
    """Best-effort: reset status back to 'idle' after wait reads a message -
    listener.py sets 'new-msg' but never clears it on its own."""
    try:
        session_data, _, _ = resolve_session(session)
        json_path = os.path.join(SESSIONS_DIR, f"{session_data['pid']}.json")
        with open(json_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        meta["status"] = "idle"
        meta["statusUpdatedAt"] = int(time.time() * 1000)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(meta, f)
    except Exception:
        pass

def cmd_wait(args):
    """Wait for unread messages (backlog or next arrival), print them, exit 0 (triggering agent wakeup)."""
    timeout = args.timeout if args.timeout > 0 else None
    # No explicit --name: auto-detect the calling harness instead of falling
    # back to the merged global inbox.
    session = args.session or os.environ.get("AGENT_PEER_NAME") or auto_session_name()

    _refuse_if_unreachable(session)

    # Guard against a second concurrent 'wait' for the same session racing
    # the same cursor. Lock releases automatically on exit/crash.
    lock_path = get_lock_path(session)
    lock_file = open(lock_path, "w")
    try:
        os.chmod(lock_path, 0o600)
    except OSError:
        pass
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        label = session or "(default)"
        print(f"❌ 'agent-peer wait' for session '{label}' is already running in another process. Close the old one before starting a new one.", file=sys.stderr)
        sys.exit(1)

    try:
        msgs = wait_for_message(session=session, timeout=timeout)
        if msgs:
            for msg in msgs:
                from_label = msg.get("from", "unknown")
                cwd_suffix = f" ({msg['from_cwd']})" if msg.get("from_cwd") else ""
                print(f"📬 [NEW MESSAGE from {from_label}{cwd_suffix}]: {msg.get('content')}")
            _reset_status_idle(session)
            sys.exit(0)
        else:
            print("Timeout waiting for message.")
            sys.exit(1)
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()

def cmd_logs(args):
    """View full detailed message logs with color formatting and live tail."""
    limit = 0 if args.all else args.limit
    show_logs(
        limit=limit,
        session=args.session,
        follow=args.follow,
        query=args.query,
        raw=args.raw,
        no_color=args.no_color
    )

def cmd_status(args):
    """Show quota/context status across every provider agent-peer bridges (agy, Claude, Codex)."""
    providers = [args.provider] if args.provider else ["agy", "claude", "codex"]

    if args.live:
        if args.json:
            print("❌ --live and --json are mutually exclusive.", file=sys.stderr)
            sys.exit(1)
        if not sys.stdout.isatty():
            print("❌ --live needs an interactive terminal. Use --json for scripts/cron instead.", file=sys.stderr)
            sys.exit(1)
        from .status_tui import run_live
        run_live(providers if args.provider else None, interval=args.interval, no_color=args.no_color)
        return

    if args.json:
        out = {}
        if "agy" in providers:
            out["agy"] = get_agy_status_dict()
        if "claude" in providers:
            out["claude"] = get_claude_status_dict()
        if "codex" in providers:
            out["codex"] = get_codex_status_dict()
        print(json.dumps(out, indent=2))
        return

    sections = []
    if "agy" in providers:
        sections.append(f"── agy (Antigravity) ──────────────────────\n{format_agy_status(as_json=False)}")
    if "claude" in providers:
        sections.append(f"── claude (Claude Code) ────────────────────\n{format_claude_status(as_json=False)}")
    if "codex" in providers:
        sections.append(f"── codex (Codex CLI) ───────────────────────\n{format_codex_status(as_json=False)}")
    print("\n\n".join(sections))

def cmd_agy_live_5h(args):
    """Internal plumbing for statusline.sh's own self-refresh, not meant
    to be run directly."""
    result, error = agy_live.fetch_live_5h_quota()
    print(json.dumps({"result": result, "error": error}))
    sys.exit(1 if error else 0)

def cmd_watch(args):
    """Watch incoming peer messages in real-time (live stream)."""
    args.follow = True
    cmd_logs(args)

def cmd_setup(args):
    """One-door skill installer: detect harnesses, pick, copy SKILL.md files."""
    entries = harness_detect.detect_all()
    by_id = {e["id"]: e for e in entries}

    if args.list:
        for e in entries:
            state = e["evidence"] if e["installed"] else "not detected"
            have = "skill installed" if e["skill_present"] else "skill missing"
            print(f"{e['id']:10} {e['label']:22} {state} [{have}]")
        return

    if args.harness:
        unknown = [h for h in args.harness if h not in by_id]
        if unknown:
            print(f"unknown harness: {', '.join(unknown)} (choose from: {', '.join(by_id)})", file=sys.stderr)
            sys.exit(2)
        selected = set(args.harness)
        initial = set()
    elif args.all:
        selected = {e["id"] for e in entries if e["installed"]}
        initial = set()
    else:
        try:
            selected = setup_tui.pick_harnesses(entries)
        except RuntimeError as exc:
            print(f"agent-peer setup: {exc}", file=sys.stderr)
            sys.exit(2)
        initial = setup_tui._initial_checked(entries)

    if args.remove:
        targets = selected if args.harness else {e["id"] for e in entries if e["skill_present"]}
        result = setup_tui.remove_skills(sorted(targets))
        for hid in result["removed"]:
            print(f"removed {hid}: {by_id[hid]['target']}")
        for hid in result["missing"]:
            print(f"nothing to remove for {hid}")
        return

    to_install = sorted(selected - initial) if not (args.all or args.harness) else sorted(selected)
    to_remove = sorted((initial - selected) & {e["id"] for e in entries if e["skill_present"]})
    if to_remove:
        result = setup_tui.remove_skills(to_remove)
        for hid in result["removed"]:
            print(f"removed {hid}: {by_id[hid]['target']}")
    if not to_install and not to_remove:
        print("nothing to do - every selected harness already has its skill installed")
        return
    result = setup_tui.install_skills(to_install)
    for hid in result["installed"]:
        print(f"installed {hid}: {by_id[hid]['target']}")
    for hid in result["skipped"]:
        print(f"skipped {hid}: bundled skill not found in this install", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(
        prog="agent-peer",
        description="Local IPC mesh for AI coding agents (Claude Code, Codex CLI, Antigravity, pi, opencode) - send messages and wake up any peer session, no polling"
    )
    parser.add_argument("-v", "--version", action="version", version=f"agent-peer {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser("list", help="List all active Claude Code / Agent sessions")
    p_list.add_argument("--cwd", default=None, help="Only show sessions whose working directory contains this substring (e.g. a project folder name)")
    p_list.set_defaults(func=cmd_list)

    # prune
    p_prune = subparsers.add_parser("prune", help="Remove registrations for sessions whose process is confirmed dead (ALIVE: no)")
    p_prune.add_argument("--force", action="store_true", help="Proceed even if every registered session shows ALIVE: no at once (normally refused - likely a sandboxed liveness check, not real)")
    p_prune.set_defaults(func=cmd_prune)

    # send
    p_send = subparsers.add_parser("send", help="Send a real-time message to a session")
    p_send.add_argument("target", help="Target session name or PID (e.g. projects-00, fe, 22748)")
    p_send.add_argument("message", help="Message text to send")
    p_send.add_argument("--priority", choices=["now", "next", "later"], default="now", help="Delivery priority (default: now)")
    p_send.add_argument("--sender", default=None, help="Sender identity name (default: $AGENT_PEER_NAME, else auto-detected from the calling harness, e.g. agy-<pid>, pi-<pid>)")
    p_send.add_argument("--await-reply", nargs="?", const=0.0, default=None, type=float, metavar="SECONDS", help="After delivering, block until the target replies (first message from them past send-time, exit 0) or the timeout lapses (exit 1). Bare flag waits indefinitely; does not consume the read cursor.")
    p_send.set_defaults(func=cmd_send)

    # listen
    p_listen = subparsers.add_parser("listen", help="Start UDS listener to receive messages from peers")
    p_listen.add_argument("--name", default=None, help="Session name to register in Claude registry (default: $AGENT_PEER_NAME, else auto-detected from the calling harness, e.g. agy-<pid>, pi-<pid>)")
    p_listen.add_argument("--codex-thread", default=None, help="This Codex session's own thread UUID (default: $CODEX_THREAD_ID). When set, 'agent-peer send' to this session delivers via native 'codex queue' instead of the file-based inbox.")
    p_listen.add_argument("--cwd", default=None, help="Working directory to register (default: the calling harness process's own cwd via lsof/procfs, not wherever this specific command happens to run - falls back to os.getcwd() if that's unavailable)")
    p_listen.add_argument("--force", action="store_true", help="Allow a second listener from this same harness session (default refuses, naming the already-running session instead of minting a '-2' dupe)")
    p_listen.set_defaults(func=cmd_listen)

    # inbox
    p_inbox = subparsers.add_parser("inbox", help="View received messages")
    p_inbox.add_argument("--name", "--session", dest="session", default=None, help="Filter inbox by session name or PID (default: all)")
    p_inbox.add_argument("--limit", type=int, default=20, help="Number of messages to show (default: 20)")
    p_inbox.add_argument("--clear", action="store_true", help="Clear all messages in inbox")
    p_inbox.set_defaults(func=cmd_inbox)

    # wait
    p_wait = subparsers.add_parser("wait", help="Wait for next incoming message and exit 0 (reactive agent trigger)")
    p_wait.add_argument("--name", "--session", dest="session", default=None, help="Wait specifically for messages sent to this session name or PID (default: $AGENT_PEER_NAME, else auto-detected from the calling harness, e.g. agy-<pid>, pi-<pid>)")
    p_wait.add_argument("--timeout", type=float, default=0, help="Timeout in seconds (0 = wait indefinitely)")
    p_wait.set_defaults(func=cmd_wait)

    # logs / log
    p_logs = subparsers.add_parser("logs", aliases=["log"], help="View formatted full message logs directly in terminal")
    p_logs.add_argument("-n", "--limit", type=int, default=20, help="Number of messages to display (default: 20)")
    p_logs.add_argument("-a", "--all", action="store_true", help="Show all message history without limit")
    p_logs.add_argument("-f", "-w", "--follow", "--watch", dest="follow", action="store_true", help="Live stream / watch new messages in real-time (like tail -f)")
    p_logs.add_argument("-s", "--name", "--session", dest="session", default=None, help="Filter messages by session name or PID")
    p_logs.add_argument("-q", "--grep", "--query", dest="query", default=None, help="Search messages containing keyword")
    p_logs.add_argument("--raw", action="store_true", help="Output raw unformatted JSON lines")
    p_logs.add_argument("--no-color", action="store_true", help="Disable ANSI color codes")
    p_logs.set_defaults(func=cmd_logs)

    # status
    p_status = subparsers.add_parser("status", help="Show quota/context status for agy, Claude Code, and/or Codex")
    p_status.add_argument("--provider", choices=["agy", "claude", "codex"], help="Limit to one provider (default: all)")
    p_status.add_argument("--json", action="store_true", help="Machine-readable JSON instead of formatted text")
    p_status.add_argument("--live", action="store_true", help="Refreshing ANSI dashboard instead of a one-shot printout (requires a TTY)")
    p_status.add_argument("--interval", type=float, default=20.0, help="Seconds between refreshes in --live mode (default: 20)")
    p_status.add_argument("--no-color", action="store_true", help="Disable ANSI color codes in --live mode")
    p_status.set_defaults(func=cmd_status)

    # agy-live-5h: plumbing statusline.sh calls for its own throttled refresh;
    # `agent-peer status` doesn't need this, it calls agy_live directly.
    p_agy_live = subparsers.add_parser("agy-live-5h", help="(internal) one-shot live 5h quota fetch, used by statusline.sh")
    p_agy_live.set_defaults(func=cmd_agy_live_5h)

    # setup
    p_setup = subparsers.add_parser("setup", help="Detect installed harnesses and install/remove their agent-peer skills")
    p_setup.add_argument("--all", action="store_true", help="Install skills for all detected harnesses without the interactive picker")
    p_setup.add_argument("--harness", action="append", default=[], metavar="ID", help="Install this harness's skill (repeatable; use with --remove to uninstall)")
    p_setup.add_argument("--remove", action="store_true", help="Remove instead of install")
    p_setup.add_argument("--list", action="store_true", help="Show detection results without changing anything")
    p_setup.set_defaults(func=cmd_setup)

    # watch
    p_watch = subparsers.add_parser("watch", help="Watch incoming peer messages in real-time (live stream)")
    p_watch.add_argument("-n", "--limit", type=int, default=10, help="Number of recent messages to show (default: 10)")
    p_watch.add_argument("-a", "--all", action="store_true", help="Show all previous messages before watching")
    p_watch.add_argument("-s", "--name", "--session", dest="session", default=None, help="Filter messages by session name or PID")
    p_watch.add_argument("-q", "--grep", "--query", dest="query", default=None, help="Search messages containing keyword")
    p_watch.add_argument("--raw", action="store_true", help="Output raw unformatted JSON lines")
    p_watch.add_argument("--no-color", action="store_true", help="Disable ANSI color codes")
    p_watch.set_defaults(func=cmd_watch)

    args = parser.parse_args()
    args.func(args)
    # Background update notice: stderr only, TTY only, cache-first - never
    # blocks a real command on network and never auto-upgrades (0022 Step 6).
    update_check.maybe_notify(__version__)

if __name__ == "__main__":
    main()
