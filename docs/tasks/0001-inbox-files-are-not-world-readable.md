# 0001 - inbox files are not world-readable

**Status:** todo
**Owner:** `agent-peer-e4`
**Files:** `agent_peer/protocol.py`, `agent_peer/inbox.py`

## Why this exists

`.dev/reviews/agy-review.md` §4.1, `.dev/reviews/pi-review.md`, `.dev/reviews/claude-review.md` §1,
and the earlier `docs/agent-peer-weaknesses-report.md` all independently flag the same thing:
`~/.agent-peer/inbox.jsonl`, `inboxes/*.jsonl`, `cursors/*.json`, and `locks/*.lock` are created
with the default umask, unlike the socket and key file which are already `chmod 0600`. Any other
local user on the machine can read the full inter-agent chat history in plain text.

## What to do

Apply `os.chmod(path, 0o600)` right after each of these files is first created/opened for
writing in `agent_peer/inbox.py` (append_inbox, _write_cursor) and wherever the lock file is
opened in `agent_peer/cli.py`. Bounded to permissions only — no format or logic changes.

## Acceptance

"Saya cek `ls -la ~/.agent-peer/inboxes/` dan semua file permission-nya 600"

## Hand-walk

<empty>
