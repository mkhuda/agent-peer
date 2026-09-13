import sys
import os
import argparse
import json
import time

from .registry import get_active_sessions, resolve_session
from .sender import send_message
from .listener import PeerListener
from .inbox import read_inbox, clear_inbox, wait_for_message
from .logs import show_logs

def cmd_list(args):
    sessions = get_active_sessions()
    if not sessions:
        print("No active Claude Code sessions found.")
        return

    print(f"{'PID':<8} {'SESSION NAME':<24} {'STATUS':<8} {'ALIVE':<6} {'SOCKET':<28} {'CWD'}")
    print("-" * 100)
    for s in sessions:
        pid = str(s.get("pid", ""))
        name = s.get("name") or "(untitled)"
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
        print(f"{pid:<8} {name:<24} {status:<8} {alive:<6} {sock:<28} {cwd}")
    print(f"\nTotal: {len(sessions)} sessions registered in ~/.claude/sessions/")

def cmd_send(args):
    try:
        res = send_message(
            target=args.target,
            content=args.message,
            priority=args.priority,
            from_name=args.sender
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
    listener = PeerListener(name=args.name)
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

def cmd_wait(args):
    """Wait until a new message arrives in the inbox, then print it and exit 0 (triggering agent wakeup)."""
    timeout = args.timeout if args.timeout > 0 else None
    session = args.session or os.environ.get("AGENT_PEER_NAME")
    msg = wait_for_message(session=session, timeout=timeout)
    if msg:
        from_label = msg.get("from", "unknown")
        print(f"📬 [NEW MESSAGE from {from_label}]: {msg.get('content')}")
        sys.exit(0)
    else:
        print("Timeout waiting for message.")
        sys.exit(1)

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

def main():
    parser = argparse.ArgumentParser(
        prog="agent-peer",
        description="Universal IPC mesh & real-time peer gateway between Claude Code sessions and external agents"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser("list", help="List all active Claude Code / Agent sessions")
    p_list.set_defaults(func=cmd_list)

    # send
    p_send = subparsers.add_parser("send", help="Send a real-time message to a session")
    p_send.add_argument("target", help="Target session name or PID (e.g. projects-00, fe, 22748)")
    p_send.add_argument("message", help="Message text to send")
    p_send.add_argument("--priority", choices=["now", "next", "later"], default="now", help="Delivery priority (default: now)")
    p_send.add_argument("--sender", default=os.environ.get("AGENT_PEER_NAME", "antigravity"), help="Sender identity name (default: antigravity or $AGENT_PEER_NAME)")
    p_send.set_defaults(func=cmd_send)

    # listen
    p_listen = subparsers.add_parser("listen", help="Start UDS listener to receive messages from peers")
    p_listen.add_argument("--name", default="antigravity", help="Session name to register in Claude registry (default: antigravity)")
    p_listen.set_defaults(func=cmd_listen)

    # inbox
    p_inbox = subparsers.add_parser("inbox", help="View received messages")
    p_inbox.add_argument("--name", "--session", dest="session", default=None, help="Filter inbox by session name or PID (default: all)")
    p_inbox.add_argument("--limit", type=int, default=20, help="Number of messages to show (default: 20)")
    p_inbox.add_argument("--clear", action="store_true", help="Clear all messages in inbox")
    p_inbox.set_defaults(func=cmd_inbox)

    # wait
    p_wait = subparsers.add_parser("wait", help="Wait for next incoming message and exit 0 (reactive agent trigger)")
    p_wait.add_argument("--name", "--session", dest="session", default=None, help="Wait specifically for messages sent to this session name or PID")
    p_wait.add_argument("--timeout", type=float, default=0, help="Timeout in seconds (0 = wait indefinitely)")
    p_wait.set_defaults(func=cmd_wait)

    # logs / log
    p_logs = subparsers.add_parser("logs", aliases=["log"], help="View formatted full message logs directly in terminal")
    p_logs.add_argument("-n", "--limit", type=int, default=20, help="Number of messages to display (default: 20)")
    p_logs.add_argument("-a", "--all", action="store_true", help="Show all message history without limit")
    p_logs.add_argument("-f", "--follow", action="store_true", help="Live stream new messages in real-time (like tail -f)")
    p_logs.add_argument("-s", "--name", "--session", dest="session", default=None, help="Filter messages by session name or PID")
    p_logs.add_argument("-q", "--grep", "--query", dest="query", default=None, help="Search messages containing keyword")
    p_logs.add_argument("--raw", action="store_true", help="Output raw unformatted JSON lines")
    p_logs.add_argument("--no-color", action="store_true", help="Disable ANSI color codes")
    p_logs.set_defaults(func=cmd_logs)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
