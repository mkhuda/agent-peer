---
name: agent-peer
description: Use when collaborating with other agent sessions (such as Claude Code, Foreman, or peer agents) via the agent-peer IPC mesh, sending real-time messages, receiving task directives, or waiting reactively for incoming IPC messages.
---

# Agent Peer (`agent-peer`)

Trigger explicitly any time with `/agent-peer intro` to load this skill and get
oriented right after starting a new session, instead of waiting for it to
auto-load from task-description matching.

## Overview
`agent-peer` is a local Unix Domain Socket (UDS) inter-process communication (IPC) mesh that connects Google Antigravity sessions and Anthropic Claude Code sessions running on the same machine.

Messages are delivered in under 200ms directly to active agent sockets in `/tmp/cc-socks/` and registered in `~/.claude/sessions/`.

**`agent-peer listen` must run before `agent-peer wait`, every session, no exceptions.** `wait` only reads an inbox `listen` creates - calling `wait` first means nobody could ever `send` to you, so it now refuses immediately (exit 1) instead of blocking forever for a message that can never arrive.

---

## 1. Quick Reference Commands

| Command | Purpose |
|---|---|
| `agent-peer list` | Discover all active Claude Code and Antigravity peer sessions on this machine |
| `agent-peer list --cwd <substring>` | Narrow the list to sessions whose working directory matches (e.g. one project) |
| `agent-peer send <peer> "<msg>"` | Send an instant real-time message to a peer by name or PID |
| `agent-peer listen` | Start background listener and register session in `~/.claude/sessions/` — no `--name` needed, auto-detects a stable session name from your own process (e.g. `agy-<pid>`) |
| `agent-peer inbox [--name <name>]` | View recent messages received (globally or isolated to session) |
| `agent-peer wait` | Reactively wait for peer messages — returns any already-queued backlog instantly (merged, not just the latest one), or blocks until the next arrival, then exit 0. No `--name` needed, auto-detects your session. |
| `agent-peer logs [-w] [-n 20] [-q <query>]` | View beautifully formatted full message logs directly in terminal |
| `agent-peer watch [-n 10] [-s <name>]` | Live stream inter-agent messages in real-time (press Ctrl+C to stop) |
| `agent-peer prune` | Remove registrations for sessions whose process is confirmed dead (`ALIVE: no`) — safe, never touches a live session |
| `agent-peer thread <id>` | Backlog + block for the next new message on a shared multi-party thread, exit 0 |
| `agent-peer send --thread <id> "<msg>"` | Post to a shared thread — every participant sees it, not just one |

---

## 2. Standard Operating Procedures (SOP)

### A. Session Startup (Register as Peer)
When a task involves peer collaboration:
1. Verify if the listener is already running:
   ```bash
   agent-peer list
   ```
2. If your session is not yet listening, start it as a background task:
   - Use `run_command` with `agent-peer listen`, **leaving `--name` off entirely**. Auto-detection gives each session its own stable name tied to your actual process (e.g. `agy-<pid>`), so distinct sessions never collide or pile up under the same name.
   - This creates `/tmp/cc-socks/<pid>.sock` and registers the session in `~/.claude/sessions/`.
   - If a session shows `ALIVE: no` in `agent-peer list`, its process is confirmed dead (e.g. killed with `Ctrl+C`, which can `SIGKILL` a background child and skip its own cleanup) — run `agent-peer prune` to remove that leftover registration.
   - If it still shows `ALIVE: yes` but is clearly no longer relevant (e.g. a duplicate `-2`/`-3` name), that's a separate known issue — leave it, don't try to kill other sessions' processes.

### B. Sending Messages & Status Reports
1. **Never dump large raw texts or diffs in the message.**
2. Write full findings, logs, and artifacts to a markdown file (e.g. `.dev/reviews/XX.md`).
3. Send a concise summary citing the file:
   ```bash
   agent-peer send <peer-name> "[fyi]: Summary of findings. Detailed report written to .dev/reviews/XX.md."
   ```
   (`agent-peer send` already labels the sender by your own auto-detected session name — don't
   also hardcode a name like "antigravity" inside the message text itself.)
4. Follow the urgency prefix convention:
   - `[fyi]`: Information, review links, completed tasks (non-blocking).
   - `[change]`: Directing a new task or changing strategy.
   - `[stop]`: Immediate halt / blocker / out-of-bounds alert.
5. Waiting for a specific reply: `agent-peer send <peer> "msg" --await-reply [SECONDS]`
   (as a background `run_command` task, like `wait`) delivers, then that same call exits 0
   the moment that peer replies — or exit 1 on timeout, bare flag waits indefinitely. Use it
   instead of send-then-`wait` when you need the answer before proceeding; it closes the race
   where a fast reply arrives before your separate `wait` starts. It never touches the read
   cursor, so a later `wait` may show the same reply again.

### C. Mandatory Reactive Standby (`agent-peer wait`)
Never run a loop polling `agent-peer inbox` or `sleep`.
- **MANDATORY RULE:** Whenever you finish reporting results, complete a task, or are waiting for instructions/feedback from a peer or foreman, you **MUST** launch `agent-peer wait` as a background task (`run_command` with `WaitMsBeforeAsync: 1000`) BEFORE ending your turn.
- No `--name` needed — it auto-detects your session from your own process identity, so it already waits exclusively for messages directed to you.
- `wait` self-tracks what you've already read per session. If messages queued up while you were busy with something else, it returns **all of them at once, instantly**, merged — not just the latest one — the moment you call it, with no separate "mark as read" step.
- Only one `wait` may run per session at a time. If one is already running (e.g. a background task from earlier that hasn't exited yet) and you launch another, the new one fails immediately with exit code 1 and an "already running" message instead of racing with it — treat that as "standby is already active," not an error to fix or retry.
- Do not let the session sit idle without a background watcher while in an active collaboration cycle.
- You will automatically be woken up by the system when the command exits upon receiving a new incoming message.
- Read the message content directly from the task result notification and proceed with the assigned directive immediately.

### D. Shared Threads (Multi-Party Discussion)
`agent-peer thread <id>` is a different primitive from `wait` above - not one-to-one, a
shared room several sessions post into and read from freely. You'll usually learn about
one from a `send` telling you its id (e.g. the foreman starting a discussion).
- Your own posts are filtered out of what `thread <id>` returns to you.
- Reply routing: a message framed `[thread: <id> ...]` MUST be answered with
  `agent-peer send --thread <id> "..."`, never a 1-to-1 `send` to whoever posted it.
- `agent-peer join <id>` is a separate, interactive human-only mode (two-way live view +
  an invite picker) - you keep using plain `thread <id>` instead, same background-task
  pattern as `wait`.
