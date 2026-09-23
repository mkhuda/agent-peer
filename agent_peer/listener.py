import os
import sys
import json
import time
import uuid
import socket
import signal
import shutil
import threading
import subprocess

# Bundle id to activate on notification click (terminal-notifier path only) -
# see docs/notification-click-target.md.
NOTIFY_ACTIVATE_BUNDLE_ID = "com.googlecode.iterm2"
from typing import Optional

from .protocol import (
    SESSIONS_DIR,
    SOCKET_DIR,
    ensure_dirs,
    get_proc_start,
    generate_peer_token,
    generate_key_filename,
    harness_session_uid,
    atomic_write_json,
)
from .inbox import append_inbox, mark_session_start
from . import compat


def _socket_address(identifier: str) -> str:
    """Address for `identifier` (a pid or name): a SOCKET_DIR path on POSIX,
    a short pipe id on Windows - compat.Listener/connect add the prefix."""
    if compat.IS_WINDOWS:
        return f"agent-peer-{identifier}"
    return os.path.join(SOCKET_DIR, f"{identifier}.sock")

class PeerListener:
    def __init__(self, name: str = "agent", cwd: Optional[str] = None, agent_type: Optional[str] = None, codex_thread_id: Optional[str] = None, force: bool = False):
        self.name = name
        self.agent_type = agent_type or "AGENT"
        self.codex_thread_id = codex_thread_id
        self.force = force
        self.harness_uid = harness_session_uid()
        self.pid = os.getpid()
        self.cwd = cwd or os.getcwd()
        self.session_id = str(uuid.uuid4())
        self.peer_token = generate_peer_token()
        self.sock_path = _socket_address(self.pid)
        self.symlink_path = _socket_address(self.name)
        self.key_filename = generate_key_filename(self.pid, self.sock_path)
        
        self.json_path = os.path.join(SESSIONS_DIR, f"{self.pid}.json")
        self.key_path = os.path.join(SESSIONS_DIR, self.key_filename)
        
        self.server_sock = None
        self.running = False
        self._cleaned_up = False

    def setup(self):
        ensure_dirs()
        proc_start = get_proc_start(self.pid)
        now_ms = int(time.time() * 1000)

        # Ensure unique session name among currently active alive sessions
        from .registry import get_active_sessions
        active_sessions = get_active_sessions()
        # Same harness session listening twice is an accident, not a new peer:
        # refuse with the old session named instead of minting a `-2` dupe.
        if self.harness_uid and not self.force:
            for s in active_sessions:
                if (s.get("alive") and s.get("pid") != self.pid
                        and s.get("harnessSessionUid") == self.harness_uid):
                    print(
                        f"❌ This session is already listening as "
                        f"'{s.get('name')}' (PID {s.get('pid')}) - not starting "
                        f"a second listener. Stop that process first if you meant "
                        f"to replace it, or run 'agent-peer listen --force' to "
                        f"keep both.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
        alive_names = {
            (s.get("name") or "").lower(): s["pid"]
            for s in active_sessions
            if s.get("alive") and s.get("pid") != self.pid
        }
        if self.name.lower() in alive_names:
            base_name = self.name
            idx = 2
            while f"{base_name}-{idx}".lower() in alive_names:
                idx += 1
            self.name = f"{base_name}-{idx}"
            self.symlink_path = _socket_address(self.name)

        # 1. Clean old socket if exists (POSIX only - a Named Pipe isn't a
        # filesystem entry, nothing to unlink on Windows).
        if not compat.IS_WINDOWS and os.path.exists(self.sock_path):
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass

        # 2. Bind the transport (AF_UNIX socket on POSIX, Named Pipe on Windows).
        self.server_sock = compat.Listener(self.sock_path)
        self.server_sock.bind()
        self.server_sock.listen(10)
        if not compat.IS_WINDOWS:
            os.chmod(self.sock_path, 0o600)  # no real file to secure on Windows - Named Pipes have their own ACL

        # 3. Create a <name>.sock symlink for convenience - POSIX only, Windows
        # needs Admin/Dev Mode for unprivileged symlinks, and doesn't need one.
        if not compat.IS_WINDOWS:
            try:
                if os.path.islink(self.symlink_path) or os.path.exists(self.symlink_path):
                    os.unlink(self.symlink_path)
                os.symlink(self.sock_path, self.symlink_path)
            except OSError:
                pass

        # 4. Write key file
        key_data = {
            "peerToken": self.peer_token,
            "procStart": proc_start,
            "pidDomain": "win32" if compat.IS_WINDOWS else "darwin"
        }
        with open(self.key_path, "w", encoding="utf-8") as f:
            json.dump(key_data, f)
        compat.secure_file(self.key_path)

        # 5. Write session json
        session_data = {
            "pid": self.pid,
            "sessionId": self.session_id,
            "cwd": self.cwd,
            "startedAt": now_ms,
            "procStart": proc_start,
            "version": "2.1.270",
            "peerProtocol": 1,
            # Marks this as one of our own listener processes (agy/pi/opencode/
            # Codex fallback) - real native Claude Code sessions never set this,
            # which is how the sender tells whether it needs to log the delivery
            # itself (nothing else will, since there's no listener.py on that side).
            "managedByAgentPeer": True,
            "agentType": self.agent_type,
            "peerFeatures": [
                "notify_idle",
                "reply_across_default_dirs",
                "artifact_yield"
            ],
            "kind": "interactive",
            "entrypoint": "cli",
            "pidDomain": "win32" if compat.IS_WINDOWS else "darwin",
            "messagingSocketPath": self.sock_path,
            "name": self.name,
            "nameSource": "user",
            "nameSince": now_ms,
            "status": "idle",
            "updatedAt": now_ms,
            "statusUpdatedAt": now_ms
        }
        if self.codex_thread_id:
            session_data["codexThreadId"] = self.codex_thread_id
        if self.harness_uid:
            session_data["harnessSessionUid"] = self.harness_uid
        atomic_write_json(self.json_path, session_data)

        # accept() only starts after setup() returns, so this cursor baseline
        # is always earlier than any message this session will actually see.
        mark_session_start(self.name)

    def cleanup(self):
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self.running = False

        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass

        for p in [self.sock_path, self.symlink_path, self.json_path, self.key_path]:
            if os.path.exists(p) or os.path.islink(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def handle_client(self, client: socket.socket):
        client.settimeout(5.0)
        authenticated = False
        buffer = ""
        try:
            while self.running:
                data = client.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="ignore")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except Exception:
                        continue

                    # Handle auth frame
                    if frame.get("type") == "auth":
                        if frame.get("token") == self.peer_token:
                            authenticated = True
                        continue

                    # Reject anything sent before a valid auth frame
                    if not authenticated:
                        continue

                    # Handle user/control frames
                    self.process_incoming_frame(frame)
        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    def process_incoming_frame(self, frame: dict):
        frame_type = frame.get("type")
        from_sender = frame.get("from", "unknown")
        priority = frame.get("priority", "normal")
        
        content = ""
        if frame_type == "user":
            content = frame.get("message", {}).get("content", "")
        elif frame_type == "control":
            content = f"[control action: {frame.get('action')}]"

        to_name = frame.get("to") or self.name
        to_pid = frame.get("to_pid") or self.pid

        record = {
            "from": from_sender,
            "from_cwd": frame.get("from_cwd"),
            "to": to_name,
            "to_pid": to_pid,
            "recipient_name": to_name,
            "recipient_pid": to_pid,
            "priority": priority,
            "type": frame_type,
            "content": content,
            "raw": frame
        }
        append_inbox(record, session_name=to_name, session_pid=to_pid)

        # Extract cleaner sender label if present in XML tags or urgency brackets
        sender_label = from_sender
        import re
        m_xml = re.search(r'from-name="([^"]+)"', content)
        m_bracket = re.search(r'\[(?:fyi|change|stop)\s+from\s+([^\]:]+)\]', content, re.IGNORECASE)
        if m_xml:
            sender_label = m_xml.group(1)
        elif m_bracket:
            sender_label = m_bracket.group(1).strip()
        elif from_sender.startswith("uds:") and "cc-socks" in from_sender:
            sender_label = os.path.basename(from_sender).replace(".sock", "")

        # Clean snippet for notifications and status line
        clean_snippet = re.sub(r'<[^>]+>', '', content) # strip xml tags
        clean_snippet = re.sub(r'\[(?:fyi|change|stop).*?\]:?\s*', '', clean_snippet, flags=re.IGNORECASE) # strip urgency headers and colon
        clean_snippet = " ".join(clean_snippet.split())[:90]

        # 1. Update session json with title & status (agent-ps sees this!)
        try:
            if os.path.exists(self.json_path):
                with open(self.json_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                meta["title"] = f"📬 [{sender_label}]: {clean_snippet}"
                meta["status"] = "new-msg"
                meta["updatedAt"] = int(time.time() * 1000)
                meta["statusUpdatedAt"] = int(time.time() * 1000)
                atomic_write_json(self.json_path, meta)
        except Exception:
            pass

        # Prefer terminal-notifier so clicking activates NOTIFY_ACTIVATE_BUNDLE_ID
        # directly - see docs/notification-click-target.md.
        try:
            title = f"📬 Message from {sender_label}"
            if shutil.which("terminal-notifier"):
                subprocess.Popen(
                    ["terminal-notifier", "-title", title, "-message", clean_snippet,
                     "-activate", NOTIFY_ACTIVATE_BUNDLE_ID, "-sound", "Glass"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            else:
                escaped_snippet = clean_snippet.replace('"', '\\"')
                escaped_title = title.replace('"', '\\"')
                script = f'display notification "{escaped_snippet}" with title "{escaped_title}" sound name "Glass"'
                subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

        # 3. Set terminal window/tab title via OSC escape sequence
        try:
            sys.stdout.write(f"\033]0;📬 [{sender_label}]: {clean_snippet}\007")
        except Exception:
            pass

        print(f"\n⚡ [MESSAGE RECEIVED] From: {sender_label} (Priority: {priority})")
        print(f"   {content}\n")
        sys.stdout.flush()

    def run(self):
        self.setup()
        self.running = True

        def _signal_handler(sig, frame):
            print("\nShutting down listener and unregistering session...")
            self.cleanup()
            sys.exit(0)

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        print(f"🚀 agent-peer listener active:")
        print(f"   Name:     {self.name}")
        print(f"   PID:      {self.pid}")
        print(f"   Socket:   {self.sock_path}")
        print(f"   Symlink:  {self.symlink_path}")
        print(f"   Registry: {self.json_path}")
        print(f"Waiting for peer messages (Ctrl+C to stop)...")
        sys.stdout.flush()

        try:
            while self.running:
                try:
                    client = self.server_sock.accept()
                    t = threading.Thread(target=self.handle_client, args=(client,), daemon=True)
                    t.start()
                except OSError:
                    break
        finally:
            self.cleanup()
