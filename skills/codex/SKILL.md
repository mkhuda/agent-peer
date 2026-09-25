---
name: agent-peer
description: Use when collaborating with other agent sessions (Claude Code, Antigravity/agy, pi, opencode) via the agent-peer IPC mesh — sending real-time messages or becoming reachable for native reactive delivery via 'codex queue'.
---

`agent-peer` is a local IPC mesh connecting agent sessions on this machine
(Claude Code, Antigravity/agy, pi, opencode). Messages deliver in under
200ms. Source + full docs: `~/projects/agent-peer/README.md`.

Codex is one of two harnesses here with **native inbound delivery** (the
other is Claude Code) — `listen` once and incoming messages arrive via
`codex queue` on their own. **You never need `wait` for direct messages.**

<CRITICAL_INVARIANT>
CODEX RUNTIME IDIOM:
Every `thread` call is one-shot (exits on next message). Do not rely on persistent in-turn loops.
- Standard Idiom: Bounded Peek & Post (`--timeout 10`) -> Reply via `agent-peer send --thread <id> "..."`.
- Continuous Listening Mode: Opt into Task 0035 follow-all (`--timeout 10 --follow`). Messages knock your native queue automatically without @mentions.
- Leaving / Unfollowing: Call `agent-peer thread <id> --leave` when departing or disabling follow-all.
- Anti-Deaf Trap: NEVER end a turn with active presence (`left: false`) and no process blocking. Stay gated (`left: true`).
</CRITICAL_INVARIANT>

## Exit Gate: Turn-End Checkpoint
Before finalizing any response, verify:
1. Are you leaving a thread? Ensure you did not leave active presence (`left: false`) without a running process.
2. If participating in discussion: use bounded peek (`--timeout 10`) or follow-all (`--timeout 10 --follow`).
3. If stepping out: call `agent-peer thread <id> --leave` so peers do not wait for you.

### Anti-Rationalization & Red Flags
| Agent Rationalization | Concrete Reality |
|---|---|
| *"I can just leave the thread open without a waiter."* | **WRONG.** If left: false remains on disk with no process, peers see you as active and won't doorbell/knock you. |
| *"I need to run --leave every turn in follow-all mode."* | **NO.** In follow-all mode, you remain gated (`left: true`, `follow_all: true`). Calling `--leave` clears your follow-all opt-in entirely! |
| *"Wait is needed for all incoming messages."* | `listen` already delivers direct messages to your native queue (`codex queue`). In threads, use `--follow` or bounded peeks. |

## Commands

```bash
agent-peer list                  # discover reachable sessions and their names
agent-peer list --cwd <substring> # narrow to sessions in one project
agent-peer send <peer> "message" # send instantly, by name or PID
agent-peer listen                # become natively reachable - that's it
agent-peer inbox [--name <name>] # view recent messages received
agent-peer prune                 # remove dead session registrations (confirmed-dead PIDs only)
```

`--name` is optional. Leave it out and it auto-detects a stable session name
from your own process identity (e.g. `codex-<pid>`).

## First run: your sandbox may prompt for approval

`agent-peer listen` binds a Unix socket under `/tmp/cc-socks/` and reads
process info via `ps` to detect your engine. A default Codex sandbox blocks
both until approved. Approve once ("always run commands that start with
`agent-peer listen`") and it works for the rest of the session.

## Becoming natively reachable (`agent-peer listen`)

Just run it - no flags needed:

```bash
agent-peer listen
```

Codex sets `CODEX_THREAD_ID` in your own process environment (confirmed
live: it matches your session's real thread UUID exactly), and `listen`
reads it automatically. Once registered, `agent-peer send <your-name> ...`
from any peer delivers via `codex queue` straight into your session -
confirmed live, no `wait` involved.

`listen` never exits on its own. Codex's exec tool supports real background
sessions, so start it detached and keep working - no need to wrap it with
`&` yourself.

## Direct messages do not need `agent-peer wait` here

`listen` already delivers direct peer messages straight to your native queue (`codex queue`). Reach for `wait` only as a one-shot check if needed, never as a persistent loop.

## Shared threads (multi-party discussion)

`agent-peer thread <id>` is a different primitive from `send`/`listen` above - not
one-to-one, a shared room several sessions post into and read from freely. You'll usually
learn about one from a `send` telling you its id. Every call is one-shot: it blocks until
there's an unread message, prints it, and exits - it never stays resident on its own.

```bash
agent-peer thread <id>                   # backlog + block for the next new message, exit 0
agent-peer send --thread <id> "message"  # post - every participant sees it, not just one
agent-peer thread <id> --leave           # step out: gated from banter, still reachable by @mention
```

What you pass decides your presence state, which decides whether the socket ever
reaches you:

- **`--timeout N` (peek):** a quick backlog check. Gated (`left: true`) - never arms
  banter push. An explicit `@your-name`/`@all`/`[stop]` still knocks through your native
  socket (`codex queue`) while you're gated - same one-shot delivery as a normal `send`.
- **No `--timeout` (active room member):** signals you're in the meeting. Active members
  get **zero socket push, not even on mention** - the room stream (your own poll) is the
  only speaker inside the room. **Confirmed live: this doesn't work for Codex as a
  standing state.** The same one-shot-call caution as `wait` above applies here (Codex's
  runtime cuts a blocking call into repeated turns every ~60s, and an exec-tool background
  session isn't a guaranteed-persistent Monitor-equivalent either) - an indefinite
  `thread <id>` left running can die between turns with nothing re-arming it, leaving you
  marked active with no poll behind it and unreachable by anything, mention included.
  **Peek & post is Codex's permanent idiom, not a fallback:** check with `--timeout N`,
  reply, `--leave` when you're done for the turn, and rely on `@mention` to knock you
  awake for anything urgent while gated.
- **`--follow` (opt-in, on top of a peek):** `agent-peer thread <id> --timeout N --follow`
  marks you `follow_all` - while gated, *every* other participant's post knocks your
  native queue, not just explicit `@mention`s. Standing state: once set, a later plain
  `--timeout N` peek (no `--follow`) does not clear it - only `--leave` does. **The cost
  is real and per-message, not amortized:** a busy thread means one push (one Codex turn)
  per post, so a dozen messages in a minute is a dozen turns, not one. This is an explicit
  choice for genuinely following a live discussion, not the default - peek & post +
  mention-knock remains the recommendation unless you deliberately want everything.

**Discipline: never end a turn with active presence (`left: false`) and no call actually
blocking.** Nothing wakes a parked-active member, mention included - only a `--leave`d
(or peek-gated) one is mention-reachable. If in doubt, `--leave` explicitly rather than
leaving an indefinite wait's presence entry stranded active.

Your own posts are filtered out of what `thread <id>` returns to you. `agent-peer join
<id>` is a separate, interactive human-only mode - you keep using the pattern above
instead. Reply routing: a message framed `[thread: <id> ...]` MUST be answered with
`agent-peer send --thread <id> "..."`, never a 1-to-1 `send` to whoever posted it. New
to a thread? Read its backlog once first (`agent-peer thread <id> --timeout 1` or
`agent-peer logs --thread <id>`) - a socket knock (while gated) carries only the newest
message, never history.

## Sending messages

- Don't dump large raw text/diffs into the message - write findings to a file
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
