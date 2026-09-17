# Changelog

## 0.2.0

Generalized from an Antigravity-only tool into a mesh any harness can join,
plus a round of security and reliability fixes found through live multi-harness
testing (agy, pi, opencode) and a 4-way collaborative review.

- `wait` now returns every queued unread message in one call via a per-session
  cursor, instead of only the latest one, and no longer replays old history on
  first use.
- A per-session file lock rejects a second concurrent `wait` instead of racing
  it, so a harness that spawns a new background call without closing the old
  one can no longer double-deliver a message.
- The peer auth token is now actually enforced during the socket handshake —
  previously it was checked but never gated on, so any frame was accepted
  regardless of authentication.
- Session name and engine are auto-detected from the calling harness (agy,
  `pi`, `opencode`, ...) by walking the parent-process chain, instead of
  defaulting to a hardcoded `antigravity`. `--name` / `--sender` /
  `$AGENT_PEER_NAME` still take priority when given.
- Sessions register with their real working directory instead of a hardcoded
  `~/projects`.
- Desktop notification clicks now activate the actual target app instead of
  always opening Script Editor (a quirk of how `osascript` notifications get
  attributed on macOS).
- `wait` warns instead of silently proceeding when there's no live listener to
  receive replies.
- Inbox, cursor, and lock files are now owner-only (`chmod 0600`/`0700`);
  previously they were created with the default umask.
- The unread cursor now survives `agent-peer inbox --clear` (reset, not
  deleted) and no longer treats a corrupted cursor file as "everything already
  read" — it falls back to replaying the backlog instead of silently dropping it.
- A session's status resets to `idle` once its queued messages are read;
  previously it stayed on `new-msg` forever after the first message.
- Ships a ready-to-install `SKILL.md` + README per harness under
  [`skills/`](./skills) (agy, pi, opencode), so a fresh clone doesn't have to
  reconstruct them from scratch.
- `agent-peer status` for agy/Claude Code quota and context numbers (see
  [`docs/status.md`](./docs/status.md)).

## 0.1.0

Initial release: a local Unix Domain Socket IPC mesh unlocking Claude Code's
native `/peer` protocol for external agents.

- `agent-peer list` / `send` / `listen` / `inbox` / `logs` / `watch`.
- `agent-peer wait` for reactive, poll-free wakeup.
- Per-session inbox isolation, session name collision resolution, and
  AGY/Claude engine badges in `list` and `logs`.
