---
name: agent-peer
description: Use when collaborating with other agent sessions (Antigravity/agy, pi, opencode, Codex) via the agent-peer IPC mesh — sending real-time messages or checking who else is reachable. Claude Code receives peer messages natively; no listen/wait step needed.
---

`agent-peer` is a local Unix Domain Socket IPC mesh connecting agent sessions
on this machine. Claude Code already has native inbound delivery (that's the
protocol this whole tool unlocks for everyone else) — a peer message from
another session just arrives directly in your context, unprompted, the
moment it's sent. You never call `listen` or `wait` yourself.

## Commands

```bash
agent-peer list                          # discover reachable sessions and their names
agent-peer send <peer> "message"         # send instantly, by name or PID
```

That's the whole surface you need. You're already registered and reachable
by name (or PID) the instant this session starts — Claude Code's own binary
handles that registration and delivery, `agent-peer` only reads/writes it.

## Sending messages

- Don't dump large raw text/diffs into the message — write findings to a file
  and send a short summary pointing at it.
- Urgency prefixes: `[fyi]` (non-blocking info), `[change]` (new task/strategy),
  `[stop]` (immediate halt/blocker).

## Talking to a non-Claude peer

Codex has native push too (via `codex queue`, once it's run `agent-peer
listen`) - a message to a Codex peer arrives just as immediately as to
another Claude Code session. Antigravity, `pi`, and opencode don't have a
native equivalent: they only receive messages while actively running
`agent-peer wait` on their side. A message you send still delivers instantly
to their inbox, but they won't act on it until their next `wait` call
returns (or a human nudges them to check).
