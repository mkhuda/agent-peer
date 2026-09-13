import socket
import time
from typing import Dict, Any, Optional

from .registry import resolve_session
from .protocol import format_auth_frame, format_user_frame

def send_message(
    target: str,
    content: str,
    priority: str = "now",
    from_name: str = "antigravity",
    from_sock: Optional[str] = None,
    timeout: float = 5.0
) -> Dict[str, Any]:
    """
    Send real-time peer message to target session.
    """
    session, sock_path, peer_token = resolve_session(target)

    # Auto-detect sender socket if sender is registered in sessions
    if from_sock is None:
        try:
            from_session, s_sock, _ = resolve_session(from_name)
            from_sock = s_sock
        except Exception:
            pass

    auth_frame = format_auth_frame(peer_token)
    user_frame = format_user_frame(
        content=content,
        from_name=from_name,
        from_sock=from_sock,
        priority=priority,
        to_name=session.get("name"),
        to_pid=session.get("pid")
    )

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    
    t0 = time.time()
    try:
        client.connect(sock_path)
        client.sendall(auth_frame.encode("utf-8"))
        time.sleep(0.04) # brief yield between frames
        client.sendall(user_frame.encode("utf-8"))
        time.sleep(0.12) # brief pause to let target process before close
    finally:
        client.close()
    
    elapsed_ms = (time.time() - t0) * 1000

    return {
        "success": True,
        "target_pid": session["pid"],
        "target_name": session.get("name"),
        "target_socket": sock_path,
        "elapsed_ms": round(elapsed_ms, 2),
        "priority": priority,
        "from": f"uds:{from_sock}" if from_sock else from_name,
        "message": content
    }
