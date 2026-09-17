---
name: agent-peer
description: Use when collaborating with other agent sessions (Claude Code, Antigravity/agy, pi, opencode) via the agent-peer IPC mesh — sending real-time messages, receiving task directives, or getting native reactive delivery via 'codex queue'.
---

`agent-peer` is a local IPC mesh connecting agent sessions on this machine
(Claude Code, Antigravity/agy, pi, opencode). Messages deliver in under
200ms. Source + full docs: `~/projects/agent-peer/README.md`.

Codex is one of two harnesses here with **native inbound delivery** (the
other is Claude Code) — register your own thread UUID once and incoming
messages arrive via `codex queue` on their own, no blocking `wait` loop
required.

## Commands

```bash
agent-peer list                                    # discover reachable sessions and their names
agent-peer send <peer> "message"                   # send instantly, by name or PID
agent-peer listen --codex-thread <your-thread-uuid> # become natively reachable (see below)
agent-peer inbox [--name <name>]                    # view recent messages received
```

`--name` is optional everywhere above. Leave it out and it auto-detects a
stable session name from your own process identity (e.g. `codex-<pid>`).

## First run: your sandbox may prompt for approval

`agent-peer listen` binds a Unix socket under `/tmp/cc-socks/` and reads
process info via `ps` to detect your engine. A default Codex sandbox blocks
both until approved. Approve once ("always run commands that start with
`agent-peer listen`") and it works for the rest of the session.

## Becoming natively reachable (`agent-peer listen --codex-thread`)

Find your own thread UUID first — there's no env var for it (checked; not
shipped), so read it from Codex's own state:

```bash
cat ~/.codex/thread-writer-locks/*.lock   # filenames are the UUIDs of currently-open threads
```

If exactly one `.lock` file exists, that's you. If more than one Codex
session is open on this machine at once, cross-check against
`~/.codex/session_index.jsonl`'s `thread_name` for one that matches your own
conversation, or ask the operator which one is yours — don't guess.

```bash
agent-peer listen --name <your-name> --codex-thread <the-uuid-you-found>
```

`listen` never exits on its own. Codex's exec tool supports real background
sessions, so start it detached and keep working — no need to wrap it with
`&` yourself. Once registered with `--codex-thread`, `agent-peer send
<your-name> ...` from any peer delivers via `codex queue` straight into your
session — confirmed live: it surfaced without ever calling `wait`.

## Fallback: reactive standby (`agent-peer wait`)

Only needed if you couldn't determine your own thread UUID, or you want to
also read anything that landed before you registered. Never poll
`agent-peer inbox` in a sleep loop — run `agent-peer wait` and let it block,
it exits the moment a message arrives:

- It self-tracks what you've already read (per-session cursor). If messages
  queued up while you were busy, it returns **all of them at once, instantly,
  merged** — not just the latest one.
- Don't pass a timeout, and don't wrap the call in a tool-level timeout
  either — a killed `wait` with no message just means calling it again,
  which is a polling loop by another name. Let the call sit open until a
  message actually arrives, however long that takes.
- Only one `wait` may run per session at a time. A second one for the same
  session fails immediately (exit code 1) instead of racing the first.
- The blocked process itself costs nothing while it sits there (plain
  OS-level block, confirmed live: 48s idle, zero output, until a message
  woke it). Don't narrate periodic "still waiting" status updates while it
  runs — that costs real tokens on your side and defeats the point of a
  zero-poll design; just let the call sit open silently.

## Sending messages

- Don't dump large raw text/diffs into the message — write findings to a file
  and send a short summary pointing at it.
- Urgency prefixes: `[fyi]` (non-blocking info), `[change]` (new task/strategy),
  `[stop]` (immediate halt/blocker).
