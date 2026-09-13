import sys
import os
import argparse
import json
import time

from .registry import get_active_sessions, resolve_session
from .sender import send_message
from .listener import PeerListener
from .inbox import read_inbox, clear_inbox

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
    if args.clear:
        clear_inbox()
        print("Inbox cleared.")
        return

    messages = read_inbox(limit=args.limit)
    if not messages:
        print("Inbox is empty.")
        return

    print(f"📬 Recent messages ({len(messages)}):\n")
    for m in messages:
        iso = m.get("received_iso", "-")
        sender = m.get("from", "unknown")
        prio = m.get("priority", "normal")
        content = m.get("content", "")
        print(f"[{iso}] From: {sender} (Priority: {prio})")
        print(f"   {content}\n")

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
    p_send.add_argument("--sender", default="antigravity", help="Sender identity name (default: antigravity)")
    p_send.set_defaults(func=cmd_send)

    # listen
    p_listen = subparsers.add_parser("listen", help="Start UDS listener to receive messages from peers")
    p_listen.add_argument("--name", default="antigravity", help="Session name to register in Claude registry (default: antigravity)")
    p_listen.set_defaults(func=cmd_listen)

    # inbox
    p_inbox = subparsers.add_parser("inbox", help="View received messages")
    p_inbox.add_argument("--limit", type=int, default=20, help="Number of messages to show (default: 20)")
    p_inbox.add_argument("--clear", action="store_true", help="Clear all messages in inbox")
    p_inbox.set_defaults(func=cmd_inbox)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
