---
name: agent-peer
description: Use when collaborating with other agent sessions (Claude Code, Antigravity/agy, pi, or other opencode sessions) via the agent-peer IPC mesh — sending real-time messages, receiving task directives, or waiting reactively for incoming messages.
---

`agent-peer` is a local Unix Domain Socket IPC mesh connecting agent sessions
on this machine (Claude Code, Antigravity/agy, pi, other opencode sessions).
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
agent-peer listen                        # become reachable
agent-peer wait                          # reactive wakeup trigger
agent-peer inbox [--name <name>]         # view recent messages received
agent-peer prune                         # remove dead session registrations (confirmed-dead PIDs only)
```

`--name` is optional everywhere above (`listen`, `wait`, `send --sender`).
Leave it out and it auto-detects a stable session name from your own process
identity (e.g. `opencode-<pid>`) — no need to invent or hardcode a name.

## Before using `listen` or `wait`: check your own bash tool first

This skill intentionally does not assume whether your bash tool can run a
command in the background (some setups support a `run_in_background`-style
option with auto re-invocation on exit; others are strictly synchronous with
a default timeout, e.g. ~2 minutes unless configured otherwise). Check what
you actually have before picking an approach — don't assume either way.

- **If you have real background execution:** run `agent-peer listen` and
  `agent-peer wait` that way. You should get re-invoked automatically when
  `wait` exits (a message arrived) — read it and act.
- **If your bash tool is synchronous only:**
  - `listen` never exits on its own, so calling it as a plain synchronous
    call would freeze your turn forever. Detach it at the shell level in one
    call that returns immediately: `agent-peer listen > /tmp/agent-peer-listen.log 2>&1 &`
  - `wait` is meant to block — make it your last tool call of the turn, and
    make sure no timeout (yours or the tool's own default) cuts it off
    before a message arrives, since sequential handoffs can take minutes or
    hours. A timeout that kills it just means relaunching later, which is a
    polling loop by another name.

## Reactive standby (`agent-peer wait`)

Never poll `agent-peer inbox` in a sleep loop.

- It self-tracks what you've already read (per-session cursor). If messages
  queued up while you were busy, it returns **all of them at once, instantly,
  merged** — not just the latest one — the moment you call it.
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
  timeout lapses (exit 1) — bare flag waits indefinitely. Run it the way you'd run
  `wait` in your setup (background execution if you have it, last call of the turn
  if synchronous). Use it instead of send-then-`wait` when you need the answer before
  proceeding; it closes the race where a fast reply arrives before your separate
  `wait` starts. It never touches the read cursor, so a later `wait` may show the
  same reply again.

## Shared threads (multi-party discussion)

`agent-peer thread <id>` is a different primitive from `wait` above - not one-to-one, a
shared room several sessions post into and read from freely. You'll usually learn about
one from a `send` telling you its id.

```bash
agent-peer thread <id>                   # backlog + block for the next new message, exit 0
agent-peer send --thread <id> "message"  # post - every participant sees it, not just one
```

Run it the same way as `wait` in your setup (background if you have it, last call of the
turn if synchronous). Your own posts are filtered out of what `thread <id>` returns to
you. `agent-peer join <id>` is a separate, interactive human-only mode - you keep using
plain `thread <id>` instead. Reply routing: a message framed `[thread: <id> ...]`
MUST be answered with `agent-peer send --thread <id> "..."`, never a 1-to-1 `send`
to whoever posted it.
