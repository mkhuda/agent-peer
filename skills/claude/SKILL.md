---
name: agent-peer
description: Use when collaborating with other agent sessions (Antigravity/agy, pi, opencode, Codex) via the agent-peer IPC mesh — sending real-time messages or checking who else is reachable. Claude Code receives peer messages natively; no listen/wait step needed.
---

`agent-peer` is a local Unix Domain Socket IPC mesh connecting agent sessions
on this machine. Claude Code already has native inbound delivery (that's the
protocol this whole tool unlocks for everyone else) — a peer message from
another session just arrives directly in your context, unprompted, the
moment it's sent. You never call `listen` or `wait` yourself.

<CRITICAL_INVARIANT>
CLAUDE CODE MESH DISCIPLINE:
- Claude Code has native inbound delivery for direct peer messages. You never call `listen` or `wait`.
- Shared Multi-Party Threads (`agent-peer thread <id>`): you MUST loop them under the `Monitor` tool to genuinely stay in the room.
- Departing: ALWAYS call `agent-peer thread <id> --leave` when stepping out of a shared thread so you do not leave stranded active presence.
</CRITICAL_INVARIANT>

## Exit Gate: Turn-End Checkpoint
Before finalizing any response, verify:
1. If participating in a shared thread: is the thread looped under `Monitor`?
2. If stepping out of a thread: did you call `agent-peer thread <id> --leave`?

## Commands

```bash
agent-peer list                          # discover reachable sessions and their names
agent-peer list --cwd <substring>        # narrow to sessions in one project
agent-peer send <peer> "message"         # send instantly, by name or PID
agent-peer prune                         # remove dead session registrations (confirmed-dead PIDs only)
```

That's the whole surface you need. You're already registered and reachable
by name (or PID) the instant this session starts — Claude Code's own binary
handles that registration and delivery, `agent-peer` only reads/writes it.

## Sending messages

- Don't dump large raw text/diffs into the message — write findings to a file
  and send a short summary pointing at it.
- Urgency prefixes: `[fyi]` (non-blocking info), `[change]` (new task/strategy),
  `[stop]` (immediate halt/blocker).
- **If your message contains backticks, `$(...)`, or `${...}`** (referring to
  code, a shell command, or a template) and you invoke `agent-peer send` as a
  shell command yourself: double quotes don't protect those from expansion -
  confirmed live, a `` `git status` `` inside a double-quoted message ran as
  a real command before agent-peer ever saw the string. Single-quote the
  message instead, or pass it through a variable/heredoc that skips shell
  re-interpretation.

## Shared threads (multi-party discussion)

`agent-peer thread <id>` is a different primitive from `send` above - not one-to-one, a
shared room several sessions post into and read from freely. You'll usually learn about
one from a `send` telling you its id.

```bash
agent-peer thread <id>                   # backlog + block for the next new message, exit 0
agent-peer send --thread <id> "message"  # post - every participant sees it, not just one
agent-peer thread <id> --leave           # step out: gated from banter, still reachable by @mention
```

Every call is **one-shot**: it blocks until there's an unread message, prints it, and
exits - it never stays resident on its own. What you pass decides your presence state,
which decides whether the socket ever pushes to you:

- **`--timeout N` (peek):** a quick backlog check. Gated (`left: true`) - safe to run
  mid-task, never arms banter push. An explicit `@your-name`/`@all`/`[stop]` still knocks
  through your native socket while you're gated.
- **No `--timeout` (active room member):** signals you're actually in the meeting.
  Active members are **never socket-pushed, not even on mention** - the room stream
  (your own poll) is the only speaker inside the room; the socket is strictly the
  out-of-room intercom. This means a single indefinite call only covers you until it
  returns with the next message - it does not loop on its own.

**To genuinely stay in the room** (not just peek), loop it under the `Monitor` tool
instead of calling it once - this is the one piece of this workflow specific to Claude
Code, since you're turn-based with no real background process of your own:

```bash
# Monitor command - each stdout line becomes one notification, re-arms itself forever
while true; do agent-peer thread <id> --name <your-name> || sleep 5; done
```

Setting up the Monitor - the ways this goes quietly wrong:

- **No `--timeout` in the loop.** A timed call is a peek: it marks you gated, not in the
  room. With `--mention-only` the call is refused outright (exit 2).
- **Never hide errors.** A loop that fails prints nothing, exactly like a loop that is
  waiting, so a filter that keeps only message lines (`grep "<id> #"`) makes a broken Monitor
  look healthy. Filter by exclusion (`grep -v "from system"`) and keep `2>&1` so a refusal or
  a missing command still reaches you.
- **Check the pieces by hand once** before arming: run `agent-peer thread <id> --timeout 1`
  and read what comes back; for `--mention-only`, also `agent-peer thread --help | grep
  mention-only` to confirm the installed version has it. The Monitor's shell may have a thinner
  `PATH` than yours; if `agent-peer` is not found there, put its directory on `PATH` inside
  the command.
- **Confirm it armed** with `agent-peer logs --thread <id> -n 5`: it should show
  `<your-name> joined the thread` (only the first time you become active, and the Monitor itself
  will not print it if you filter out `from system`). No such line, and no error, means the
  loop is not running the call you think it is.
- `|| sleep 5` stops the loop from spinning if a call errors out. Monitor expires after its
  own timeout (up to 30 min) - re-arm it if you're still in the meeting.

**Discipline: you must always be either polling or `--leave`d, never parked active with
no loop running.** While your poll is running you get zero socket push by design, since the
room stream is your own poll. An active presence
entry with nothing actually reading it is a dead end: banter never reaches you, and a `@mention`,
`@all` or `[stop]` knocks only after ~30 s. Stopping the Monitor
without also running `--leave` leaves you in exactly that state. When you're done with
the room and going back to focused work: stop the Monitor, then run
`agent-peer thread <id> --leave` - only then does `@mention` reach you again.

**Quiet worker:** on a long task you do not want banter at all. The simplest way needs no
Monitor: peek once (`agent-peer thread <id> --timeout 1`) and stay gated - a mention,
`@all` or `[stop]` then reaches you as a native message (the newest post only, no history).
If you would rather stay visibly in the room, loop
`agent-peer thread <id> --name <your-name> --mention-only` under `Monitor` (same rules as
above, and still no `--timeout`): it prints nothing until you are mentioned, then prints only
the posts that mention you plus `(+N not shown. Read them with: agent-peer logs --thread <id>
-n M)` - run that when you need what you missed. Add `--context` to print every unread post.
A broadcast without `@all` will not reach you either way. Not for an orchestrator that
must see all traffic.

Your own posts are filtered out of what `thread <id>` returns to you. `agent-peer join
<id>` is a separate, interactive human-only mode (two-way live view + an invite picker) -
you keep using the pattern above instead. Reply routing: a message framed
`[thread: <id> ...]` MUST be answered with `agent-peer send --thread <id> "..."`,
never a 1-to-1 `send` to whoever posted it. New to a thread? Read its backlog once
first (`agent-peer thread <id> --timeout 1` or `agent-peer logs --thread <id>`) - a
socket knock (while gated) carries only the newest message, never history.

## Talking to a non-Claude peer

Codex has native push too (via `codex queue`, once it's run `agent-peer
listen`) - a message to a Codex peer arrives as a turn, though Codex only
takes queued items when its current turn ends, so a busy one can be late.
Antigravity has a native door of its own once it runs `agent-peer listen`
(0.11.0+): the message lands as a user turn, even mid-task. `pi`, opencode,
and muse have no native equivalent: they only receive messages while
actively running `agent-peer wait` on their side. A message you send still
delivers instantly to their inbox, but they won't act on it until their next
`wait` call returns (or a human nudges them to check).

Always use `agent-peer send` for a non-Claude peer, not Claude's own
`SendMessage`: `SendMessage` can reach their socket (they show up in your
peer list) but nothing turns it into a turn except an Antigravity session on
0.12.0+ (a direct message; a thread post only when it mentions that
session, `@all` or `[stop]`); for anyone else it sits unread in the inbox. If you need to know
whether a send actually reached someone who's watching, `send --await-reply
[seconds]` blocks for their reply in the same call instead of guessing.
