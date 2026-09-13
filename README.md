# agent-peer

Universal IPC mesh and real-time peer gateway between Claude Code sessions and external agents (Google Antigravity, Hermes, Cursor Agent, and custom scripts).

## Overview

Claude Code features a native cross-session messaging subsystem (`/peer`) powered by Unix Domain Sockets (`/tmp/cc-socks/<pid>.sock`) and per-session authentication tokens (`~/.claude/sessions/<pid>.<token_hash>.key`).

`agent-peer` unlocks this protocol for any agent or shell environment:
- **Instant message delivery (< 10ms)** into running Claude Code sessions without polling.
- **Session discovery**: Inspects all active Claude Code sessions, their states, sockets, and directories.
- **Two-way duplex mesh**: Allows external agents (like Google Antigravity) to register as full peers, enabling Claude Code to reply back directly via `/peer antigravity`.
- **Zero heavy dependencies**: Written in pure, modern Python (3.10+) using the standard library.

## Installation

### Via `uv` (Recommended)

```bash
cd ~/projects/agent-peer
uv tool install --editable . --force
```

This creates the global `agent-peer` command in `~/.local/bin/agent-peer`.

### Direct Python

```bash
python3 -m pip install -e .
```

## Usage

### 1. List active sessions

```bash
agent-peer list
```

Example output:
```text
PID      SESSION NAME             STATUS   ALIVE  SOCKET                       CWD
----------------------------------------------------------------------------------------------------
22748    projects-00              idle     yes    22748.sock                   ~/projects
86622    ottoshare-factory-fe     busy     yes    86622.sock                   .../ottoshare-factory
60526    music-search-engine-tas  busy     yes    60526.sock                   .../ottoshare-factory
3386     ottoshare-factory-d8     busy     yes    3386.sock                    .../ottoshare-factory
```

### 2. Send real-time message to a session

Send by name or PID:
```bash
agent-peer send projects-00 "Halo foreman, ini arahan review dari arsitek!"
```

With custom priority (`now`, `next`, `later`) and sender identity:
```bash
agent-peer send fe "[stop] Harap hentikan edit di file X, lihat spesifikasi di docs." --priority now --sender antigravity
```

### 3. Start two-way listener (Register Antigravity as a peer)

To allow Claude Code sessions to discover and reply to Antigravity:
```bash
agent-peer listen --name antigravity
```

When active, Claude Code sessions can simply type in their terminal:
```text
/peer antigravity Laporan: modul caption sudah siap ditinjau.
```

### 4. Inspect inbox

```bash
agent-peer inbox
agent-peer inbox --limit 10
agent-peer inbox --clear
```

## Architecture & Protocol

Claude Code enforces process ownership verification before accepting IPC connections:
1. **PID validation**: Checks if the target process is running.
2. **Process start time verification**: Matches `LC_ALL=C TZ=UTC ps -o lstart= -p <pid>`.
3. **Auth token handshake**: First line must be `{"type": "auth", "token": "<peerToken>"}\n`.
4. **Message frame**: Followed by `{"type": "user", "priority": "now", "from": "...", "message": {"content": "..."}}\n`.

`agent-peer` automatically handles this entire handshake, socket binding, token generation, and graceful session lifecycle cleanup.

## License

MIT License.
