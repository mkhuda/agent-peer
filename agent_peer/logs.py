import os
import sys
import time
import json
import re
import shutil
from typing import Optional, Dict, List, Tuple

from .protocol import INBOX_FILE, get_session_inbox_path
from .registry import get_active_sessions
from .thread import read_thread, read_thread_presence

# ANSI Color Codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
UNDERLINE = "\033[4m"

# Foreground colors
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"

# High intensity
BRIGHT_RED = "\033[91m"
BRIGHT_GREEN = "\033[92m"
BRIGHT_YELLOW = "\033[93m"
BRIGHT_BLUE = "\033[94m"
BRIGHT_MAGENTA = "\033[95m"
BRIGHT_CYAN = "\033[96m"
BRIGHT_WHITE = "\033[97m"

# Background
BG_RED = "\033[41m"
BG_YELLOW = "\033[43m"
BG_CYAN = "\033[46m"

def supports_color() -> bool:
    """Check if output stream supports color formatting."""
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()

def get_session_cache() -> Dict[str, Dict[str, str]]:
    """Map PID and lower session name to {name, type, pid} info."""
    cache = {}
    try:
        sessions = get_active_sessions()
        for s in sessions:
            pid = s.get("pid")
            name = s.get("name") or f"pid-{pid}"
            agent_type = s.get("agentType") or ("AGY" if "antigravity" in name.lower() or "agy" in name.lower() else "Claude")
            info = {
                "name": name,
                "type": agent_type,
                "pid": str(pid) if pid else ""
            }
            if pid:
                cache[str(pid)] = info
            cache[name.lower()] = info
    except Exception:
        pass
    return cache

def resolve_agent_type(name: str, cache: Dict[str, Dict[str, str]]) -> str:
    """Infer whether session is AGY or Claude."""
    clean = name.lower().strip()
    if clean in cache:
        return cache[clean].get("type", "Claude")
    m = re.search(r'(\d+)', clean)
    if m and m.group(1) in cache:
        return cache[m.group(1)].get("type", "Claude")
    if "antigravity" in clean or "agy" in clean:
        return "AGY"
    if clean and clean not in ("peer", "unknown", "all"):
        return "Claude"
    return ""

def format_agent_badge(agent_type: str, use_color: bool = True) -> str:
    """Generate subtle colored badge for AGY or Claude."""
    if not agent_type:
        return ""
    if use_color:
        if agent_type.upper() == "AGY":
            return f"{BRIGHT_BLUE}[AGY]{RESET}"
        elif agent_type.upper() == "CLAUDE":
            return f"{BRIGHT_YELLOW}[Claude]{RESET}"
        return f"{DIM}[{agent_type}]{RESET}"
    return f"[{agent_type}]"

def extract_sender_info(record: dict, session_cache: Dict[str, Dict[str, str]]) -> Tuple[str, str, str]:
    """
    Extract readable sender name, raw origin, and agent type.
    Returns: (display_name, raw_from, agent_type)
    """
    raw_from = record.get("from", "unknown")
    content = record.get("content", "")
    sender_name = ""

    # 1. XML attribute: from-name="XYZ"
    m_xml = re.search(r'from-name="([^"]+)"', content)
    if m_xml:
        sender_name = m_xml.group(1).strip()

    # 2. Urgency header: [fyi from XYZ] or [change from XYZ]
    if not sender_name:
        m_urgency = re.search(r'\[(?:fyi|change|stop)\s+from\s+([^\]:]+)\]', content, re.IGNORECASE)
        if m_urgency:
            sender_name = m_urgency.group(1).strip()

    # 3. Socket PID resolution
    if not sender_name:
        m_sock = re.search(r'(\d+)\.sock', raw_from)
        if m_sock:
            pid_str = m_sock.group(1)
            if pid_str in session_cache:
                sender_name = session_cache[pid_str]["name"]
            else:
                sender_name = f"pid-{pid_str}"

    if not sender_name:
        sender_name = raw_from.replace("uds:", "").replace("/tmp/cc-socks/", "")

    agent_type = resolve_agent_type(sender_name, session_cache)
    return sender_name, raw_from, agent_type

def extract_recipient_info(record: dict, session_cache: Dict[str, Dict[str, str]]) -> Tuple[str, str]:
    """Extract readable recipient name and agent type."""
    recip_name = record.get("recipient_name") or record.get("to")
    if not recip_name and isinstance(record.get("raw"), dict):
        recip_name = record["raw"].get("to")

    if not recip_name and record.get("recipient_pid"):
        pid_str = str(record["recipient_pid"])
        if pid_str in session_cache:
            recip_name = session_cache[pid_str]["name"]
        else:
            recip_name = f"pid-{pid_str}"

    # Should be rare - the listener always populates these fields. Fall back
    # to something honest rather than guessing.
    if not recip_name or recip_name == "peer":
        recip_name = "unknown"

    agent_type = resolve_agent_type(recip_name, session_cache)
    return recip_name, agent_type

def clean_message_content(content: str) -> str:
    """Strip XML wrappers while preserving inner markdown text."""
    content = content.strip()
    # Strip <cross-session-message ...> ... </cross-session-message>
    m = re.match(r'^<cross-session-message[^>]*>(.*)</cross-session-message>$', content, re.DOTALL)
    if m:
        content = m.group(1).strip()
    return content

def format_log_entry(record: dict, session_cache: Dict[str, Dict[str, str]], use_color: bool = True) -> str:
    """Format a single message record into a beautifully styled terminal card."""
    iso = record.get("received_iso", "-")
    priority = record.get("priority", "now")
    sender, raw_from, sender_type = extract_sender_info(record, session_cache)
    recipient, recip_type = extract_recipient_info(record, session_cache)
    raw_content = record.get("content", "")
    content = clean_message_content(raw_content)

    term_width = shutil.get_terminal_size((88, 24)).columns
    divider_len = min(term_width, 100)

    # Detect urgency tag
    urgency_tag = ""
    m_tag = re.search(r'\[(fyi|change|stop)(?:\s+from\s+[^\]]+)?\]:?', content, re.IGNORECASE)
    if m_tag:
        tag_word = m_tag.group(1).lower()
        if tag_word == "stop":
            urgency_tag = f"{BG_RED}{BRIGHT_WHITE}{BOLD} STOP {RESET}" if use_color else "[STOP]"
        elif tag_word == "change":
            urgency_tag = f"{BRIGHT_YELLOW}{BOLD}[CHANGE]{RESET}" if use_color else "[CHANGE]"
        elif tag_word == "fyi":
            urgency_tag = f"{BRIGHT_CYAN}[FYI]{RESET}" if use_color else "[FYI]"

    s_badge = format_agent_badge(sender_type, use_color)
    r_badge = format_agent_badge(recip_type, use_color)
    from_cwd = record.get("from_cwd")

    if use_color:
        time_str = f"{DIM}{iso}{RESET}"
        from_str = f"{BOLD}{BRIGHT_CYAN}{sender}{RESET}"
        if s_badge:
            from_str += f" {s_badge}"
        if from_cwd:
            from_str += f" {DIM}({from_cwd}){RESET}"
        arrow_str = f"{DIM}──►{RESET}"
        to_str = f"{BOLD}{BRIGHT_GREEN}{recipient}{RESET}"
        if r_badge:
            to_str += f" {r_badge}"
        
        # Priority color
        if priority == "now":
            prio_str = f"{BRIGHT_RED}{BOLD}now{RESET}"
        elif priority == "next":
            prio_str = f"{BRIGHT_YELLOW}next{RESET}"
        else:
            prio_str = f"{DIM}{priority}{RESET}"

        div_bar = f"{DIM}{'━' * divider_len}{RESET}"
        sub_div = f"{DIM}{'─' * divider_len}{RESET}"
        
        header = f" {BOLD}🕒 {time_str}  │  {from_str}  {arrow_str}  {to_str}  │  Priority: {prio_str}"
        if urgency_tag:
            header += f"  {urgency_tag}"
    else:
        div_bar = "━" * divider_len
        sub_div = "─" * divider_len
        from_str = f"{sender} {s_badge}".strip()
        if from_cwd:
            from_str += f" ({from_cwd})"
        to_str = f"{recipient} {r_badge}".strip()
        header = f" 🕒 {iso}  |  {from_str}  -->  {to_str}  |  Priority: {priority}"
        if urgency_tag:
            header += f"  {urgency_tag}"

    # Format content lines with a gentle 2-space margin
    indented_lines = []
    for line in content.splitlines():
        indented_lines.append(f"  {line}")
    body_text = "\n".join(indented_lines)

    card = f"{div_bar}\n{header}\n{sub_div}\n{body_text}\n"
    return card

def show_logs(
    limit: int = 20,
    session: Optional[str] = None,
    follow: bool = False,
    query: Optional[str] = None,
    raw: bool = False,
    no_color: bool = False
):
    """Display inter-agent communication logs with live follow option."""
    use_color = not no_color and supports_color()
    path = get_session_inbox_path(session) if session else INBOX_FILE

    if not os.path.exists(path):
        if session:
            print(f"No log file found for session '{session}'.")
        else:
            print(f"No log file found at {path}. No messages sent yet.")
        return

    session_cache = get_session_cache()

    def read_records() -> List[dict]:
        records = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            pass
        return records

    all_records = read_records()

    # Filter by session if requested and reading global file
    if session and not get_session_inbox_path(session):
        sess_lower = session.lower()
        all_records = [
            r for r in all_records
            if sess_lower in (r.get("recipient_name") or "").lower()
            or sess_lower in (r.get("from") or "").lower()
            or sess_lower in str(r.get("recipient_pid", "")).lower()
        ]

    # Filter by query search
    if query:
        q_lower = query.lower()
        all_records = [r for r in all_records if q_lower in r.get("content", "").lower()]

    if limit > 0 and not follow:
        records_to_show = all_records[-limit:]
    else:
        records_to_show = all_records[-limit:] if (follow and limit > 0) else all_records

    # Print banner
    if not raw:
        target_info = f" (filtered: '{session}')" if session else " (all sessions)"
        if use_color:
            print(f"\n{BOLD}{BRIGHT_WHITE}📬 AGENT-PEER MESSAGE LOGS{RESET}{DIM}{target_info}{RESET}")
            if follow:
                print(f"{DIM}Streaming real-time messages... (Press Ctrl+C to stop){RESET}\n")
            else:
                print(f"{DIM}Showing last {len(records_to_show)} messages from {path}{RESET}\n")
        else:
            print(f"\n📬 AGENT-PEER MESSAGE LOGS{target_info}")
            if follow:
                print("Streaming real-time messages... (Press Ctrl+C to stop)\n")
            else:
                print(f"Showing last {len(records_to_show)} messages from {path}\n")

    for r in records_to_show:
        if raw:
            print(json.dumps(r))
        else:
            print(format_log_entry(r, session_cache, use_color=use_color))

    sys.stdout.flush()

    # Live streaming mode (-f / --follow)
    if follow:
        try:
            with open(path, "r", encoding="utf-8") as f:
                # Seek to end
                f.seek(0, os.SEEK_END)
                while True:
                    line = f.readline()
                    if not line:
                        time.sleep(0.1)
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue

                    # Refresh session cache periodically
                    session_cache = get_session_cache()

                    # Apply filters
                    if session:
                        sess_lower = session.lower()
                        from_str = record.get("from", "").lower()
                        rec_str = (record.get("recipient_name") or "").lower()
                        pid_str = str(record.get("recipient_pid", ""))
                        if sess_lower not in from_str and sess_lower not in rec_str and sess_lower not in pid_str:
                            continue

                    if query and query.lower() not in record.get("content", "").lower():
                        continue

                    if raw:
                        print(json.dumps(record))
                    else:
                        print(format_log_entry(record, session_cache, use_color=use_color))
                    sys.stdout.flush()
        except KeyboardInterrupt:
            print("\nLog streaming stopped.")
            return

def _fmt_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3600)}h"

def format_thread_presence_header(thread_id: str, use_color: bool = True) -> str:
    presence = read_thread_presence(thread_id)
    if not presence:
        return ""
    now = time.time()
    parts = []
    for name, info in sorted(presence.items()):
        age = _fmt_age(max(0.0, now - info.get("last_seen", 0)))
        # 0035: follow-all is a standing state that changes what a gated
        # member receives - surface it so it's observable, not a hidden
        # behavior others have to infer from watching what gets through.
        suffix = " [follow-all]" if isinstance(info, dict) and info.get("follow_all") else ""
        parts.append(f"{name} ({age} ago){suffix}")
    label = f"Present: {', '.join(parts)}"
    return f"{DIM}{label}{RESET}" if use_color else label

def _thread_urgency_tag(content: str, use_color: bool) -> str:
    m = re.search(r'\[(fyi|change|stop)(?:\s+from\s+[^\]]+)?\]:?', content, re.IGNORECASE)
    if not m:
        return ""
    tag = m.group(1).lower()
    if not use_color:
        return f"[{tag.upper()}]"
    if tag == "stop":
        return f"{BG_RED}{BRIGHT_WHITE}{BOLD} STOP {RESET}"
    if tag == "change":
        return f"{BRIGHT_YELLOW}{BOLD}[CHANGE]{RESET}"
    return f"{BRIGHT_CYAN}[FYI]{RESET}"

def format_thread_entry(record: dict, use_color: bool = True, viewer: Optional[str] = None) -> str:
    """A thread record has no recipient/priority (it's shared, not
    one-to-one) - a simpler card than format_log_entry's inbox cards.
    `viewer` (the reader's own participant name) gets a visually distinct
    card - a double divider and a different accent color - so a busy
    thread stays easy to scan for "did I already say that" at a glance.
    System lifecycle events (0031) render as a dimmed ambient line."""
    if record.get("from") == "system" or record.get("type") == "event":
        line = f"— {record.get('content', '')} —"
        return f"{DIM}{line}{RESET}\n" if use_color else f"{line}\n"
    seq = record.get("seq")
    time_str = time.strftime("%H:%M:%S", time.localtime(record.get("ts", 0)))
    sender = record.get("from", "unknown")
    content = record.get("content", "")
    term_width = shutil.get_terminal_size((88, 24)).columns
    divider_len = min(term_width, 100)
    urgency_tag = _thread_urgency_tag(content, use_color)
    is_viewer = viewer is not None and sender == viewer

    if use_color:
        accent = BRIGHT_GREEN if is_viewer else BRIGHT_CYAN
        div_char = "═" if is_viewer else "━"
        div_bar = f"{DIM}{div_char * divider_len}{RESET}"
        you_badge = f" {DIM}(you){RESET}" if is_viewer else ""
        header = f" {BOLD}#{seq}{RESET} {DIM}{time_str}{RESET}  {BOLD}{accent}{sender}{RESET}{you_badge}"
    else:
        div_char = "═" if is_viewer else "━"
        div_bar = div_char * divider_len
        you_badge = " (you)" if is_viewer else ""
        header = f" #{seq} {time_str}  {sender}{you_badge}"
    if urgency_tag:
        header += f"  {urgency_tag}"

    body = "\n".join(f"  {line}" for line in content.splitlines())
    return f"{div_bar}\n{header}\n{body}\n"

def show_thread_logs(
    thread_id: str,
    limit: int = 20,
    follow: bool = False,
    query: Optional[str] = None,
    raw: bool = False,
    no_color: bool = False,
    viewer: Optional[str] = None,
):
    """Human-readable view of a shared thread - a viewer can watch a
    meeting happen without needing to 'thread <id>' (wait) on it. `viewer`
    highlights that participant's own messages (see format_thread_entry)."""
    use_color = not no_color and supports_color()
    all_records = read_thread(thread_id)
    records = all_records
    if query:
        q_lower = query.lower()
        records = [r for r in records if q_lower in r.get("content", "").lower()]
    records_to_show = records[-limit:] if limit > 0 else records

    if not raw:
        if use_color:
            print(f"\n{BOLD}{BRIGHT_WHITE}📖 THREAD '{thread_id}'{RESET}")
        else:
            print(f"\nTHREAD '{thread_id}'")
        header = format_thread_presence_header(thread_id, use_color)
        if header:
            print(header)
        status = "Streaming... (Press Ctrl+C to stop)" if follow else f"Showing last {len(records_to_show)} of {len(records)} messages"
        print(f"{DIM}{status}{RESET}\n" if use_color else f"{status}\n")

    for r in records_to_show:
        print(json.dumps(r) if raw else format_thread_entry(r, use_color=use_color, viewer=viewer))
    sys.stdout.flush()

    if not follow:
        return

    # From the unfiltered thread, not records_to_show - a query that matches
    # nothing (or only an old message) must not make follow replay history.
    last_seq = all_records[-1]["seq"] if all_records else 0
    try:
        while True:
            for r in read_thread(thread_id):
                seq = r.get("seq", 0)
                if seq <= last_seq:
                    continue
                last_seq = seq
                if query and query.lower() not in r.get("content", "").lower():
                    continue
                print(json.dumps(r) if raw else format_thread_entry(r, use_color=use_color, viewer=viewer))
                sys.stdout.flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nThread streaming stopped.")
        return
