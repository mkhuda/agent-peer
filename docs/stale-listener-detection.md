# Plan: Stale Listener Detection in `agent-peer list`

## Problem

Real example (`agent-peer list`):

```text
72769    antigravity      AGY   new-msg  yes  72769.sock  ~/projects   <- actively in use
71277    antigravity-2    AGY   new-msg  yes  71277.sock  ~/projects   <- user closed session in UI
```

`antigravity-2` (PID 71277) remains reported as ALIVE=yes even though the user closed that session in the UI. Root cause **is not** PID-reuse:

- `procStart` in `~/.claude/sessions/71277.json` = `Sun Sep 13 21:31:59 2026`
- `LC_ALL=C TZ=UTC ps -o lstart= -p 71277` = `Sun Sep 13 21:31:59 2026` (identical)

So the process is literally still alive, not a PID reused by another process. `agent-peer list` technically reports "alive" **correctly**.

## Why "Detached Detection" is NOT the Solution (Tried, Incorrect)

Initially suspected: stale processes could be identified by having no controlling terminal (TTY `??`) because the UI session that closed did not send SIGTERM/SIGINT.
Verified directly with `ps -o pid,ppid,tty,stat -p <pid>` for BOTH processes:

```text
PID    PPID  TTY   STAT   COMMAND
71277  1     ??    S      agent-peer listen --name antigravity   (stale)
72769  1     ??    S      agent-peer listen --name antigravity   (actively in use)
```

Both are **identical**: `TTY=??`, `PPID=1` (reparented to `launchd`), `STAT=S`. The actively used process is also detached — because that is how Antigravity spawns `agent-peer listen` (background, without a terminal), not a sign of abandonment. Thus **no OS-level attribute (TTY/PPID/STAT) can distinguish "stale" vs "actively used"** — both are technically legitimate processes, but one is no longer relevant to user intent, which cannot be inferred from `ps`.

## Actual Root Cause

`cleanup()` (`listener.py:121-138`) — which removes socket, symlink, session JSON, and key files — only gets invoked via `_signal_handler` when the process receives `SIGINT`/`SIGTERM` (`listener.py:262-263`), or via normal loop exit. Closing a session in the UI does not send those signals to the `agent-peer listen` process running separately in the background — so the process lives forever until manually killed, even when no longer used.

Another triggering factor: `PeerListener.setup()` (`listener.py:53-58`) — when a name is already used by another active session, it appends a suffix (`-2`, `-3`, ...) instead of replacing/terminating the old one. Stale listeners accumulate continuously without automatic replacement.

## Another Dead End: Checking Parent Process

Idea: store `PPID` when `agent-peer listen` starts, then treat as "detached/stale" if that parent is no longer alive. Verified directly, failed on two levels:

1. `ps aux | grep -i antigravity` — **no local Antigravity application process runs at all** that can serve as a "session owner" baseline. There is no target to check for existence.
2. `ps -ef` for both listeners (stale and active) shows `PPID=1` (`launchd`) — detached spawned processes get reparented to launchd instantly upon birth, so recording `os.getppid()` at `setup()` start yields `1`, not the original caller process. There is no time window to capture that signal.

Conclusion: the boundary of "session closed in UI" is an internal state of the application that never manifests as any observable OS signal (process/parent) for `agent-peer`. Do not attempt parent-liveness approaches again — no channel exists.

## Signals AVAILABLE for Detection (Imperfect, but Useful)

Since no definitive OS signal exists, detection must rely on application-level heuristics:

1. **Duplicate Name Groups** — sessions with the same base name pattern (`antigravity`, `antigravity-2`, `antigravity-3`, ...) are strong candidates for "one of them being stale", because they only arise via the suffix mechanism in `listener.py:53-58`, not manual user input. Multiple listeners coexisting under the same base name is itself a signal.
2. **Recency** — `statusUpdatedAt`/`updatedAt` in session JSON. The most recently updated session in a duplicate group is most likely relevant; older ones are suspicious (not absolute proof — long idle valid listeners are also possible).
3. Insufficient signals for safe **auto-kill** — both are hints for humans, not a basis for automatic deletion (touching running processes + deleting registrations is destructive, never do automatically without confirmation).

## Plan

Not "fix detached detection" (since that signal is invalid) — instead:

1. **`agent-peer list`**: visually mark sessions in a duplicate name group (e.g., `dup?` badge next to the name), sorted/highlighted by most recently active in the group, so users see potential stale candidates at a glance without manual `ps`.
2. **New command `agent-peer stop <name-or-pid>`**: send SIGTERM to the target listener process — triggering its own `cleanup()` gracefully (rather than deleting registration files externally, maintaining consistency with `listener.py` lifecycle). Official replacement for manual `kill <pid>`.
3. **Optional, evaluate later**: `agent-peer prune` — list all duplicate name groups + recommendations on stale ones (based on recency), prompt user confirmation per process before `stop`. Never auto-run without approval.

## Minimal Scope of Changes

- `registry.py`: add helper to group-by base-name (regex `^(.*?)(-\d+)?$` on `name`) and compute most recent per group.
- `cli.py:cmd_list`: use helper for visual badge.
- `cli.py` + `listener.py`: `stop` command sending `SIGTERM` via `os.kill(pid, signal.SIGTERM)` after resolving target via `registry.resolve_session`.
- Touch no `sender.py`, `inbox.py`, `protocol.py`, or socket handshake.

## Status

Planned, not yet implemented. Pending user confirmation before execution.
