import shutil
import subprocess
import time
from typing import Dict, Any

from .registry import resolve_session
from .protocol import format_auth_frame, format_user_frame
from .inbox import append_inbox
from . import compat

def _resolve_sender_cwd(from_name: str):
    """Best-effort: the sender's own registered cwd, purely informational -
    never used for addressing or session identity."""
    try:
        sender_session, _, _ = resolve_session(from_name)
        return sender_session.get("cwd")
    except Exception:
        return None

def _with_sender_header(content: str, from_name: str, from_cwd) -> str:
    """A real native Claude Code recipient only ever sees 'content' - its own
    binary renders the message without surfacing the 'from'/'from_cwd'
    fields, so the sender label has to live inside the text itself here."""
    header = f"[from {from_name}" + (f" · {from_cwd}]" if from_cwd else "]")
    return f"{header}\n{content}"

def _send_via_codex_queue(session: Dict, thread_id: str, content: str, from_name: str, from_cwd, priority: str) -> Dict[str, Any]:
    """Deliver natively via 'codex queue', bypassing the file-based inbox entirely."""
    if not shutil.which("codex"):
        raise RuntimeError("Target is a Codex session with a registered thread, but the 'codex' binary is not on PATH.")

    # codex queue only carries plain text - no structured 'from'/'from_cwd'
    # fields exist for it, so the sender label has to live in the text itself.
    wire_content = _with_sender_header(content, from_name, from_cwd)

    t0 = time.time()
    result = subprocess.run(
        ["codex", "queue", "--thread", thread_id, "--message", wire_content],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise RuntimeError(f"'codex queue' failed: {(result.stderr or result.stdout).strip()}")
    elapsed_ms = (time.time() - t0) * 1000

    to_name = session.get("name")
    to_pid = session["pid"]
    # No receiving listener processes this delivery (codex queue bypasses the
    # socket entirely), so log it here or 'watch'/'logs'/'inbox' never see it.
    append_inbox(
        {
            "from": from_name,
            "from_cwd": from_cwd,
            "to": to_name,
            "to_pid": to_pid,
            "recipient_name": to_name,
            "recipient_pid": to_pid,
            "priority": priority,
            "type": "user",
            "content": wire_content,
            "raw": {"transport": "codex-queue", "thread_id": thread_id}
        },
        session_name=to_name,
        session_pid=to_pid
    )

    return {
        "success": True,
        "target_pid": session["pid"],
        "target_name": session.get("name"),
        "target_socket": f"codex-queue:{thread_id}",
        "elapsed_ms": round(elapsed_ms, 2),
        "priority": priority,
        "from": from_name,
        "message": content
    }

def send_message(
    target: str,
    content: str,
    priority: str = "now",
    from_name: str = "agent",
    timeout: float = 5.0
) -> Dict[str, Any]:
    """
    Send real-time peer message to target session.
    """
    session, sock_path, peer_token = resolve_session(target)
    from_cwd = _resolve_sender_cwd(from_name)

    codex_thread_id = session.get("codexThreadId")
    if session.get("agentType") == "CODEX" and codex_thread_id:
        return _send_via_codex_queue(session, codex_thread_id, content, from_name, from_cwd, priority)

    is_native_claude = not session.get("managedByAgentPeer")
    wire_content = _with_sender_header(content, from_name, from_cwd) if is_native_claude else content

    auth_frame = format_auth_frame(peer_token)
    user_frame = format_user_frame(
        content=wire_content,
        from_name=from_name,
        # Only include the extra field for our own listener.py-managed
        # targets - a real native Claude Code recipient parses this frame
        # with its own binary, whose schema tolerance is unverified.
        from_cwd=from_cwd if not is_native_claude else None,
        priority=priority,
        to_name=session.get("name"),
        to_pid=session.get("pid")
    )

    client = compat.connect(sock_path, timeout=timeout)

    t0 = time.time()
    try:
        client.sendall(auth_frame.encode("utf-8"))
        time.sleep(0.04) # brief yield between frames
        client.sendall(user_frame.encode("utf-8"))
        time.sleep(0.12) # brief pause to let target process before close
    finally:
        client.close()

    elapsed_ms = (time.time() - t0) * 1000

    if is_native_claude:
        # Real native Claude Code session - its own binary receives this over
        # the socket, never our listener.py, so nothing else will log it.
        to_name = session.get("name")
        to_pid = session["pid"]
        append_inbox(
            {
                "from": from_name,
                "from_cwd": from_cwd,
                "to": to_name,
                "to_pid": to_pid,
                "recipient_name": to_name,
                "recipient_pid": to_pid,
                "priority": priority,
                "type": "user",
                "content": wire_content,
                "raw": {"transport": "uds-native-claude"}
            },
            session_name=to_name,
            session_pid=to_pid
        )

    return {
        "success": True,
        "target_pid": session["pid"],
        "target_name": session.get("name"),
        "target_socket": sock_path,
        "elapsed_ms": round(elapsed_ms, 2),
        "priority": priority,
        "from": from_name,
        "message": content
    }
