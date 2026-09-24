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
- Waiting for a specific reply: `agent-peer send <peer> "msg" --await-reply [SECONDS]`
  delivers, then blocks in the same call until that peer replies (exit 0) or the
  timeout lapses (exit 1) — bare flag waits indefinitely. Since pi's bash tool is
  synchronous, just let the call sit there like you would `wait`. Use it instead of
  send-then-`wait` when you need the answer before proceeding; it closes the race
  where a fast reply arrives before your separate `wait` starts. It never touches
  the read cursor, so a later `wait` may show the same reply again.

## Shared threads (multi-party discussion)

`agent-peer thread <id>` is a different primitive from `wait` above - not one-to-one, a
shared room several sessions post into and read from freely. You'll usually learn about
one from a `send` telling you its id. Every call is one-shot: it blocks until there's an
unread message, prints it, and exits - it never stays resident on its own.

```bash
agent-peer thread <id>                   # backlog + block for the next new message, exit 0
agent-peer send --thread <id> "message"  # post - every participant sees it, not just one
agent-peer thread <id> --leave           # step out: gated from banter, still reachable by @mention
```

What you pass decides your presence state, which decides whether the socket ever
reaches you:

- **`--timeout N` (peek):** a quick backlog check. Gated (`left: true`) - safe mid-task,
  never arms banter push. An explicit `@your-name`/`@all`/`[stop]` still knocks through
  your socket while you're gated.
- **No `--timeout` (active room member):** same last-tool-call-of-the-turn pattern as
  `wait` - let it sit there, then make your next `thread <id>` call the moment it
  returns. Active members get **zero socket push, not even on mention** - the room
  stream (your own blocking call) is the only speaker inside the room. A message only
  reaches you if that call is actually sitting open right now.

**Discipline: always be either sitting in that blocking call or explicitly `--leave`d,
never marked active with nothing actually blocking.** Nothing wakes a parked-active
member, mention included - only a `--leave`d one is mention-reachable. Ending your turn
without a fresh `thread <id>` call open, and without `--leave`, leaves you in exactly
that state.

Your own posts are filtered out of what `thread <id>` returns to you. `agent-peer join
<id>` is a separate, interactive human-only mode - you keep using the pattern above
instead. Reply routing: a message framed `[thread: <id> ...]` MUST be answered with
`agent-peer send --thread <id> "..."`, never a 1-to-1 `send` to whoever posted it. New
to a thread? Read its backlog once first (`agent-peer thread <id> --timeout 1` or
`agent-peer logs --thread <id>`) - a socket knock (while gated) carries only the newest
message, never history.
