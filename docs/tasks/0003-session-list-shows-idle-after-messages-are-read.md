# 0003 - session list shows idle after messages are read

**Status:** todo
**Owner:** `agent-peer-e4`
**Files:** `agent_peer/inbox.py`, `agent_peer/cli.py`

## Why this exists

`pi-review.md` §2.1, verified directly against the running system: `listener.py` writes
`status: "new-msg"` into `~/.claude/sessions/<pid>.json` when a message arrives, but nothing ever
writes `status: "idle"` back after the message is actually read. Confirmed live: `pi-98661`,
`antigravity-test`, and `opencode-15297` all still show `new-msg` in `agent-peer list` long after
every message was consumed via `wait`. Anyone using the STATUS column to decide "does this session
still have something unread" is being misled — it can never go back to idle on its own.

## What to do

When `wait_for_message` successfully returns messages (cursor actually advances), also update that
session's `~/.claude/sessions/<pid>.json` — set `status: "idle"` and refresh `statusUpdatedAt` — if
a registration file for that session name exists. Best-effort: if no listener/registration is found
for the name (see `cli.py:_warn_if_unreachable`), skip silently, same as today.

## Acceptance

"Saya `wait` sampai dapat pesan, lalu `agent-peer list` nampilin status `idle` lagi"

## Hand-walk

<empty>
