# Exploration Report: Weaknesses & Potential Issues in `agent-peer`

## Overview
This document contains an in-depth analysis of the `agent-peer` codebase architecture and implementation (`agent_peer/*.py`). The analysis focuses on potential **Security**, **Reliability / Race Condition**, and **Architectural Design** issues not previously covered in existing documentation ([docs/stale-listener-detection.md](docs/stale-listener-detection.md) and [docs/wait-unread-cursor.md](docs/wait-unread-cursor.md)).

---

## 1. Security & Authentication Issues (Security & Access Control)

### 1.1 Unauthenticated Read Access on Inbox Files (`~/.agent-peer/inbox.jsonl`)
* **Code Location:** [inbox.py](agent_peer/inbox.py#L22-L34), [protocol.py](agent_peer/protocol.py#L15-L20)
* **Finding:**
  When `ensure_dirs()` triggers the creation of the `~/.agent-peer` directory, the directory and `inbox.jsonl` / `inboxes/*.jsonl` files are created using default system *umask* permissions (typically `0644` / `0755`). Unlike sockets in `/tmp/cc-socks/*.sock` and key files in `~/.claude/sessions/*.key` which are explicitly set to `chmod 0600`, inbox and cursor files do not have explicit restrictive permissions set.
* **Impact:**
  Other local users on the system (or processes with general user privileges) can read the entire inter-agent chat history stored in plain-text JSON format in `~/.agent-peer/inbox.jsonl`.

### 1.2 Potential Token Leaks / Lack of Socket Verification on Response Flow
* **Code Location:** [sender.py](agent_peer/sender.py#L22-L28), [registry.py](agent_peer/registry.py#L50-L65)
* **Finding:**
  The `sender.send_message` function reads authentication tokens (`peerToken`) directly from the registration file `~/.claude/sessions/<pid>.<hash>.key`. However, when the sender authenticates to the target UDS socket, the target only matches the string token (`frame.get("token") == self.peer_token`) in [listener.py](agent_peer/listener.py#L161-L164) without verifying the sender's OS credentials (such as `SO_PEERCRED` / `LOCAL_PEERCRED` on Unix Domain Sockets).
* **Impact:**
  Any local process capable of reading the `~/.claude/sessions/` directory can impersonate any sender and send instructions to any agent socket.

---

## 2. Reliability & Concurrency Issues (Reliability & Race Conditions)

### 2.1 Race Condition on Concurrent Inbox Writes (Non-Atomic File I/O)
* **Code Location:** [inbox.py](agent_peer/inbox.py#L21-L34)
* **Finding:**
  Incoming messages in [inbox.py](agent_peer/inbox.py) are appended using `open(..., "a")` directly from the listener thread handler (`handle_client` -> `process_incoming_frame` -> `append_inbox`).
* **Impact:**
  If two incoming messages arrive simultaneously from two different client threads (or two separate listeners), writes to `inbox.jsonl` or `inboxes/<session>.jsonl` can suffer *interleaving* (truncated/intermixed characters) due to un-locked writes (missing `fcntl.flock`). This causes `json.loads(line)` in `read_inbox()` to fail parsing the line and silently drop the message.

### 2.2 Cursor Desynchronization on File Reset / Manual Truncation
* **Code Location:** [inbox.py](agent_peer/inbox.py#L60-L90)
* **Finding:**
  The `last_read_at` cursor relies on an epoch float (`time.time()`). When `agent-peer inbox --clear` is executed, the inbox file is truncated (`w`), but the cursor file in `~/.agent-peer/cursors/<session>.json` is neither reset nor deleted.
* **Impact:**
  If a new message arrives after `--clear`, but bears a timestamp less than or equal to the previous `last_read_at` (e.g. during fast test loops or close timestamp resolution), the new message risks being skipped by `get_unread()`.

### 2.3 Symlink Poisoning & Deadlock Risks on `/tmp/cc-socks/`
* **Code Location:** [listener.py](agent_peer/listener.py#L75-L80)
* **Finding:**
  In [listener.py](agent_peer/listener.py#L78), the listener attempts `os.unlink(self.symlink_path)` and `os.symlink(self.sock_path, self.symlink_path)`. Because `/tmp` is a shared sticky directory across local users, static symlinks like `/tmp/cc-socks/antigravity.sock` are vulnerable to permission conflicts if created by another user or if socket name collisions occur.

---

## 3. Architectural Design & Usability Issues

### 3.1 Synchronous Blocking Operations in AppleScript & GUI Triggers
* **Code Location:** [listener.py](agent_peer/listener.py#L235-L242)
* **Finding:**
  Upon receiving a message, `listener.py` executes `subprocess.Popen(["osascript", "-e", script])` to display macOS desktop notifications. Even though `Popen` is non-blocking, under high load or when macOS `notificationcenterd` hangs, repeated subprocess forks without throttling can cause zombie process accumulation or resource exhaustion.

### 3.2 Unguaranteed Resource Cleanup on Unexpected Crash
* **Code Location:** [listener.py](agent_peer/listener.py#L257-L263)
* **Finding:**
  The `cleanup()` function is only registered for `SIGINT` and `SIGTERM` signal handlers. If the process encounters a `SIGKILL` (`kill -9`), segfault, or unexpected crash, the session JSON registration in `~/.claude/sessions/<pid>.json` and the socket file in `/tmp/cc-socks/` will linger permanently as stale files until manually cleaned up.

---

## Summary of Recommended Fixes
1. **File Permissions:** Apply `os.chmod(..., 0o700)` to `~/.agent-peer/` and `0o600` to all `.jsonl` inbox and `.json` cursor files.
2. **Atomic Writes & File Locking:** Use `fcntl.flock(f, fcntl.LOCK_EX)` when appending lines in `append_inbox()` and updating cursors in `_write_cursor()`.
3. **Cursor Synchronization & Cleanup:** Ensure `clear_inbox()` also resets or deletes the corresponding cursor file.
