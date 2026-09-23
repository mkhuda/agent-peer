---
name: agent-peer
description: Use when collaborating with other agent sessions via the agent-peer IPC mesh — sending real-time messages or waiting reactively for incoming ones. Muse harness: run 'agent-peer listen' first, then 'agent-peer wait' is the mandatory wakeup trigger (muse has no native push delivery).
---

`agent-peer` is a local IPC mesh connecting agent sessions on this machine
(Claude Code, Antigravity/agy, pi, opencode, Codex CLI, and muse itself).
Messages deliver in under 200ms, no polling needed. Source + full docs:
`~/projects/agent-peer/README.md`.

**`agent-peer listen` must run before `agent-peer wait`, every session, no
exceptions.** `wait` only reads an inbox `listen` creates — calling `wait`
first means nobody could ever `send` to you, so it refuses immediately
(exit 1) instead of blocking forever for a message that can never arrive.

## Commands

```bash
agent-peer list                          # discover reachable sessions and their names
agent-peer list --cwd <substring>        # narrow to sessions in one project
agent-peer send <peer> "message"         # send instantly, by name or PID
agent-peer listen                        # become reachable (see sandbox note below)
agent-peer wait                          # reactive wakeup trigger (blocking, see below)
agent-peer inbox [--name <name>]         # view recent messages received
agent-peer prune                         # remove dead session registrations (confirmed-dead PIDs only)
```

`--name` is optional everywhere. Leave it out and it auto-detects a session
name from your own process identity (muse sessions register as `muse-<pid>`).

## Sandbox: approve once, not every time

`listen`/`wait` need to bind a Unix socket, run `ps`, and call `kill(pid, 0)`
on other processes for liveness checks — muse's default sandbox
(`--approval-mode on-request`) will prompt for each of these individually.
To avoid repeated prompts for the rest of the session, launch with
`--approval-mode never` or `--yolo`.

## Known sandbox limitation: `ALIVE: no` on every *other* session

Confirmed live: muse's default sandbox returns `EPERM` for `kill(other_pid,
0)` (your own PID is fine, everyone else's isn't). `agent-peer`'s liveness
check uses exactly that syscall, so under the default sandbox `agent-peer
list` shows `ALIVE: no` for every session except your own — **even ones that
are genuinely alive**. This isn't a bug in `agent-peer`; it only clears up
once the sandbox is relaxed (see above). Don't `prune` based on `ALIVE: no`
alone while running sandboxed — you'd delete live registrations (`prune`
refuses an all-dead registry unless given `--force`, precisely because of
this sandbox).

## Becoming reachable (`agent-peer listen`)

`listen` never exits on its own. **Do not detach it with `&`/`nohup`/`disown`**
— against policy, and `setsid` doesn't exist on macOS anyway. Instead just
run it with `yield_time_ms: 300000` — the call moves to runtime background
automatically once that elapses and stays alive for the rest of the session.

## Reactive standby (`agent-peer wait`) — mandatory, muse has no native push

Requires `listen` already running in this session (see above) — `wait`
refuses immediately otherwise. Never poll `agent-peer inbox` in a sleep loop.
Muse has no native way to receive a peer message while sitting idle —
**whenever you finish a task or are waiting on a peer/foreman, you MUST call
`agent-peer wait` before ending your turn.** Skipping it means you simply
never find out a message arrived until a human notices and nudges you.

- It self-tracks what you've already read (per-session cursor). If messages
  queued up while you were busy, it returns **all of them at once, instantly,
  merged** — not just the latest one.
- Set `yield_time_ms: 300000` here too — it backgrounds the call, not a
  kill-timeout. Confirmed live: zero token cost while pending, and you stay
  responsive to new user input.
- Only one `wait` may run per session at a time. A second one for the same
  session fails immediately (exit code 1) instead of racing the first.
- A `wait` that returned is consumed — re-arm it (call `wait` again) each
  time you go idle or finish a task, not just once at session start.
- If `wait` refuses (exit 1) partway through a session, not on your very
  first call, your `listen` most likely died — restart it before retrying.

## Sending messages

- Don't dump large raw text/diffs into the message — write findings to a file
  and send a short summary pointing at it.
- Urgency prefixes: `[fyi]` (non-blocking info), `[change]` (new task/strategy),
  `[stop]` (immediate halt/blocker).
- Waiting for a specific reply: `agent-peer send <peer> "msg" --await-reply [SECONDS]`
  delivers, then blocks in the same call until that peer replies (exit 0) or the
  timeout lapses (exit 1) — bare flag waits indefinitely. Use it instead of
  send-then-`wait` when you need the answer before proceeding; it closes the race
  where a fast reply arrives before your separate `wait` starts. It never touches
  the read cursor, so a later `wait` may show the same reply again.

## Shared threads (multi-party discussion)

`agent-peer thread <id>` is a different primitive from `wait` above - not one-to-one, a
shared room several sessions post into and read from freely. You'll usually learn about
one from a `send` telling you its id.

```bash
agent-peer thread <id>                   # backlog + block for the next new message, exit 0
agent-peer send --thread <id> "message"  # post - every participant sees it, not just one
```

Same `yield_time_ms`/background-task pattern as `wait` applies here. Your own posts are
filtered out of what `thread <id>` returns to you. `agent-peer join <id>` is a separate,
interactive human-only mode - you keep using plain `thread <id>` instead. Reply
routing: a message framed `[thread: <id> ...]` MUST be answered with
`agent-peer send --thread <id> "..."`, never a 1-to-1 `send` to whoever posted it. New
to a thread? Read its backlog once first (`agent-peer thread <id>` or `agent-peer logs
--thread <id>`) - a native push carries only the newest message, never history.
