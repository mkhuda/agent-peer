import sys
import os
import argparse
import json
import time
import fcntl

from .registry import get_active_sessions, resolve_session
from .sender import send_message
from .listener import PeerListener
from .inbox import read_inbox, clear_inbox, wait_for_message
from .logs import show_logs
from .agy_status import format_agy_status, get_agy_status_dict
from .claude_status import format_claude_status, get_claude_status_dict
from .protocol import get_lock_path, auto_session_name, detect_harness_identity, SESSIONS_DIR
from . import agy_live
from . import __version__

def cmd_list(args):
    sessions = get_active_sessions()
    if not sessions:
        print("No active Claude Code sessions found.")
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

def cmd_send(args):
    sender = args.sender or os.environ.get("AGENT_PEER_NAME") or auto_session_name()
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

def cmd_listen(args):
    name = args.name or os.environ.get("AGENT_PEER_NAME") or auto_session_name()
    # Engine is detected independently of the session name, even if --name
    # was given manually, so it always reflects the real calling harness.
    harness, _ = detect_harness_identity()
    codex_thread_id = args.codex_thread or os.environ.get("CODEX_THREAD_ID")
    listener = PeerListener(
        name=name,
        agent_type=(harness.upper() if harness else None),
        codex_thread_id=codex_thread_id
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
        print(f"[{iso}] From: {sender} (Priority: {prio})")
        print(f"   {content}\n")

def _warn_if_unreachable(session: str):
    """Warn if no listener is registered for this session - 'wait' only
    reads the inbox, it never opens a socket itself. Never auto-starts one."""
    try:
        resolve_session(session)
    except Exception:
        print(f"⚠️  No listener running for session '{session}' - you are not reachable via 'agent-peer send' right now. Run 'agent-peer listen' (detached) if you want to be. Proceeding to check for already-queued messages...", file=sys.stderr)

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

    _warn_if_unreachable(session)

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
                print(f"📬 [NEW MESSAGE from {from_label}]: {msg.get('content')}")
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
    """Show quota/context status across every provider agent-peer bridges (agy, Claude)."""
    providers = [args.provider] if args.provider else ["agy", "claude"]

    if args.json:
        out = {}
        if "agy" in providers:
            out["agy"] = get_agy_status_dict()
        if "claude" in providers:
            out["claude"] = get_claude_status_dict()
        print(json.dumps(out, indent=2))
        return

    sections = []
    if "agy" in providers:
        sections.append(f"── agy (Antigravity) ──────────────────────\n{format_agy_status(as_json=False)}")
    if "claude" in providers:
        sections.append(f"── claude (Claude Code) ────────────────────\n{format_claude_status(as_json=False)}")
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

def main():
    parser = argparse.ArgumentParser(
        prog="agent-peer",
        description="Local IPC mesh for AI coding agents (Claude Code, Codex CLI, Antigravity, pi, opencode) - send messages and wake up any peer session, no polling"
    )
    parser.add_argument("-v", "--version", action="version", version=f"agent-peer {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser("list", help="List all active Claude Code / Agent sessions")
    p_list.set_defaults(func=cmd_list)

    # send
    p_send = subparsers.add_parser("send", help="Send a real-time message to a session")
    p_send.add_argument("target", help="Target session name or PID (e.g. projects-00, fe, 22748)")
    p_send.add_argument("message", help="Message text to send")
    p_send.add_argument("--priority", choices=["now", "next", "later"], default="now", help="Delivery priority (default: now)")
    p_send.add_argument("--sender", default=None, help="Sender identity name (default: $AGENT_PEER_NAME, else auto-detected from the calling harness, e.g. agy-<pid>, pi-<pid>)")
    p_send.set_defaults(func=cmd_send)

    # listen
    p_listen = subparsers.add_parser("listen", help="Start UDS listener to receive messages from peers")
    p_listen.add_argument("--name", default=None, help="Session name to register in Claude registry (default: $AGENT_PEER_NAME, else auto-detected from the calling harness, e.g. agy-<pid>, pi-<pid>)")
    p_listen.add_argument("--codex-thread", default=None, help="This Codex session's own thread UUID (default: $CODEX_THREAD_ID). When set, 'agent-peer send' to this session delivers via native 'codex queue' instead of the file-based inbox.")
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
    p_status = subparsers.add_parser("status", help="Show quota/context status for agy and/or Claude Code")
    p_status.add_argument("--provider", choices=["agy", "claude"], help="Limit to one provider (default: both)")
    p_status.add_argument("--json", action="store_true", help="Machine-readable JSON instead of formatted text")
    p_status.set_defaults(func=cmd_status)

    # agy-live-5h: plumbing statusline.sh calls for its own throttled refresh;
    # `agent-peer status` doesn't need this, it calls agy_live directly.
    p_agy_live = subparsers.add_parser("agy-live-5h", help="(internal) one-shot live 5h quota fetch, used by statusline.sh")
    p_agy_live.set_defaults(func=cmd_agy_live_5h)

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

if __name__ == "__main__":
    main()
