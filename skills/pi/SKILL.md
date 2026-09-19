---
name: agent-peer
description: Use when collaborating with other agent sessions (Claude Code, Antigravity/agy, opencode, or other pi sessions) via the agent-peer IPC mesh — sending real-time messages, receiving task directives, or waiting reactively for incoming messages.
---

`agent-peer` is a local Unix Domain Socket IPC mesh connecting agent sessions
on this machine (Claude Code, Antigravity/agy, opencode, other pi sessions).
Messages deliver in under 200ms, no polling needed. Source + full docs:
`~/projects/agent-peer/README.md`.

**`agent-peer listen` must run before `agent-peer wait`, every session, no
exceptions.** `wait` only reads an inbox `listen` creates - calling `wait`
first means nobody could ever `send` to you, so it now refuses immediately
(exit 1) instead of blocking forever for a message that can never arrive.

## Commands

```bash
agent-peer list                          # discover reachable sessions and their names
agent-peer list --cwd <substring>        # narrow to sessions in one project
agent-peer send <peer> "message"         # send instantly, by name or PID
agent-peer listen                        # become reachable (must be detached, see below)
agent-peer wait                          # reactive wakeup trigger (blocking tool call, see below)
agent-peer inbox [--name <name>]         # view recent messages received
agent-peer prune                         # remove dead session registrations (confirmed-dead PIDs only)
```

`--name` is optional everywhere above (`listen`, `wait`, `send --sender`).
Leave it out and it auto-detects a stable session name from your own process
identity (e.g. `pi-<pid>`) — no need to invent or hardcode a name.

pi's bash tool is synchronous with no background-task feature — a tool call
blocks until the process it ran exits. `listen` and `wait` need opposite
handling because of this:

## Becoming reachable (`agent-peer listen`)

`listen` never exits on its own, so calling it as a normal tool call would
freeze your turn forever. Detach it at the shell level instead, in one tool
call that returns immediately:

```bash
agent-peer listen > /tmp/agent-peer-listen.log 2>&1 &
```

Do this once per session, if a task involves peer collaboration. It
registers you in `~/.claude/sessions/` so other sessions can
`agent-peer send <your-name> ...` to reach you, and keeps running detached
in the background for the rest of the session.

## Reactive standby (`agent-peer wait`)

Never poll `agent-peer inbox` in a sleep loop. Instead, whenever you finish
reporting results or are waiting on a peer/foreman for the next instruction,
make `agent-peer wait` (no flags) your **last tool call of the turn** — it's
meant to block, so let the call itself sit there rather than detaching it:

- It self-tracks what you've already read (per-session cursor). If messages
  queued up while you were busy, it returns **all of them at once, instantly,
  merged** — not just the latest one — the moment you call it.
- Otherwise it blocks until the next message arrives, however long that
  takes (minutes or hours for a sequential handoff). Don't pass `--timeout`:
  the bash tool's own `timeout` param would `SIGKILL` the whole process tree
  once it elapses (no message = hard kill, not a clean return), and you'd
  have to notice and manually call `wait` again — a polling loop by another
  name. Leave both agent-peer's `--timeout` and the tool's own `timeout`
  unset so the call blocks indefinitely and your turn simply stays open
  until a message actually arrives.
- Only one `wait` may run per session at a time. If another one is already
  in flight for your session and you call it again, it fails immediately
  (exit code 1, "already running") instead of racing with it — that means
  standby is already active, not something to fix or retry.

## Sending messages

- Don't dump large raw text/diffs into the message — write findings to a file
  and send a short summary pointing at it.
- Urgency prefixes: `[fyi]` (non-blocking info), `[change]` (new task/strategy),
  `[stop]` (immediate halt/blocker).
