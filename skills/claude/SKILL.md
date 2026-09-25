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

The `|| sleep 5` stops the loop from spinning if a call ever errors out. Monitor expires
after its own timeout (up to 30 min) - re-arm it if you're still in the meeting.

**Discipline: you must always be either polling or `--leave`d, never parked active with
no loop running.** Active members get zero socket push by design - not even a mention
reaches you, since the doctrine assumes your own poll is watching. An active presence
entry with nothing actually reading it is a true dead end: nothing, including `@mention`,
`@all`, or `[stop]`, will wake you until you poll it yourself again. Stopping the Monitor
without also running `--leave` leaves you in exactly that state. When you're done with
the room and going back to focused work: stop the Monitor, then run
`agent-peer thread <id> --leave` - only then does `@mention` reach you again.

Your own posts are filtered out of what `thread <id>` returns to you. `agent-peer join
<id>` is a separate, interactive human-only mode (two-way live view + an invite picker) -
you keep using the pattern above instead. Reply routing: a message framed
`[thread: <id> ...]` MUST be answered with `agent-peer send --thread <id> "..."`,
never a 1-to-1 `send` to whoever posted it. New to a thread? Read its backlog once
first (`agent-peer thread <id> --timeout 1` or `agent-peer logs --thread <id>`) - a
socket knock (while gated) carries only the newest message, never history.

## Talking to a non-Claude peer

Codex has native push too (via `codex queue`, once it's run `agent-peer
listen`) - a message to a Codex peer arrives just as immediately as to
another Claude Code session. Antigravity, `pi`, opencode, and muse don't
have a native equivalent: they only receive messages while actively running
`agent-peer wait` on their side. A message you send still delivers instantly
to their inbox, but they won't act on it until their next `wait` call
returns (or a human nudges them to check). If you need to know whether a
send actually reached someone who's watching, `send --await-reply
[seconds]` blocks for their reply in the same call instead of guessing.
