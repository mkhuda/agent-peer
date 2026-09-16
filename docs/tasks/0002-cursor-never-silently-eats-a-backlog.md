# 0002 - cursor never silently eats a backlog

**Status:** todo
**Owner:** `agent-peer-e4`
**Files:** `agent_peer/inbox.py`

## Why this exists

`pi-review.md` §3.6: if `~/.agent-peer/cursors/<session>.json` fails to parse (corrupted write,
partial write, etc.), `_read_cursor` falls back to `return time.time()` — which silently treats
every already-unread message as "old" and permanently un-recoverable. That is the exact opposite
of the "never lose a message" guarantee the backlog-merge design (`docs/wait-unread-cursor.md`) was
built for.

Separately, `agy-review.md`, `opencode-review.md`, and `claude-review.md` all note that
`agent-peer inbox --clear` empties the inbox file but never touches the cursor file, which is
surprising (though not currently a real bug in the forward-time case — see docs/wait-unread-cursor.md).

## What to do

1. In `_read_cursor`, change the corrupt/unparseable fallback from `time.time()` to `0` (never seen
   anything = replay everything, safe direction to fail in), and print a warning to stderr so the
   corruption itself doesn't go unnoticed.
2. In `clear_inbox`, also delete/reset the matching cursor file for that session so "clear" behaves
   the way a person would expect: forget everything, including what was or wasn't read.

## Acceptance

"Saya jalankan `agent-peer inbox --clear` lalu kirim pesan baru, dan `wait` menangkapnya"

## Hand-walk

<empty>
