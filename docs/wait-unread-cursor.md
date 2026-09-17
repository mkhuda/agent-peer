# Plan: Unread Cursor for `agent-peer wait`

## Problem

`wait` currently (`inbox.py:wait_for_message`) uses **baseline count-at-call-time**:

```python
def wait_for_message(session=None, timeout=None):
    initial_count = len(read_inbox(session=session))
    while True:
        msgs = read_inbox(session=session)
        if len(msgs) > initial_count:
            return msgs[-1]
        ...
```

Two concrete consequences of this design:

1. **Backlog lost after restart.** `wait` runs as a blocking tool call in agy's agentic loop. If agy is busy executing another task (not running `wait`), the previous `wait` process has exited (previous tool call finished / turn changed). Messages arriving during that window remain recorded in the inbox file, but as soon as `wait` is invoked again after the task completes, a new baseline is computed from total count **including** those accumulated messages — so `wait` does not return immediately, and instead waits for a genuinely new subsequent message.
2. **Only returns 1 message, remaining messages lost permanently.** If multiple messages arrive within a single poll cycle (100ms), `return msgs[-1]` only returns the last one. The next call's baseline already includes all of them, so skipped messages can never be replayed via `wait`.

Combined effect: agy loses awareness of messages arriving while busy with another task, and `wait` re-invoked after task completion does not automatically "claim" that backlog.

## Target Behavior

The agent does not need to perform an explicit "mark as read" action. Whenever agy invokes `wait` (at any time, whether idle or just finished another task):

- If an unread message backlog exists for this session → immediately `return` all of them (merged, not just 1), **without entering polling/blocking mode**.
- If no backlog exists → operate as before: blocking poll until a new message arrives, then return.
- In both cases, the cursor automatically advances after `return` — effectively an "auto flag read" without the agent needing to manage read/unread state explicitly.

## Design

The cursor is stored **per session**, based on **timestamp** (not line count) to withstand `agent-peer inbox --clear`:

```text
~/.agent-peer/cursors/<name-or-pid>.json
{ "last_read_at": <epoch float> }
```

Why timestamp, not count:
- Count is fragile if the inbox file is `--clear`ed (index resets to 0, risking re-reading old messages if the file builds up again from scratch).
- Timestamp (`received_at`, already present on every record — see `inbox.py:append_inbox`) is sufficient to filter `received_at > last_read_at`, and stays consistent even if the file is cleared.

Changes in `inbox.py`:

```python
def get_unread(session=None):
    cursor = _read_cursor(session)          # default: current time, if cursor file doesn't exist
    msgs = read_inbox(session=session)
    return [m for m in msgs if m.get("received_at", 0) > cursor]

def wait_for_message(session=None, timeout=None):
    unread = get_unread(session)
    if unread:
        _write_cursor(session, unread[-1]["received_at"])
        return unread                        # list, not 1 message
    t0 = time.time()
    while True:
        unread = get_unread(session)
        if unread:
            _write_cursor(session, unread[-1]["received_at"])
            return unread
        if timeout is not None and (time.time() - t0) >= timeout:
            return None
        time.sleep(0.1)
```

Default cursor when file does not exist: **current exact time** (not 0) — so the first `wait` call does not suddenly replay all historical logs as "unread".

## API Changes

- `wait_for_message` returns `List[dict]` (1 or many), not `Optional[dict]`.
- `cli.py:cmd_wait` iterates the list when printing, rather than assuming 1 message.
- Optional new command: `agent-peer unread` (similar to `inbox` but only un-cursored items, without advancing cursor — for manual inspection without side effects) — **not yet decided, evaluate if needed after base version runs**.

## Scope of Changes (Minimal)

- `inbox.py`: add `_read_cursor`/`_write_cursor`/`get_unread`, modify `wait_for_message`.
- `cli.py`: `cmd_wait` handles list.
- Touch no `sender.py`, `listener.py`, `protocol.py`, `registry.py`, socket handshake, or frame format — pure inbox reading side logic.

## Status

**Implemented** (`protocol.py`: `CURSORS_DIR`/`get_cursor_path`; `inbox.py`: `_read_cursor`/`_write_cursor`/`get_unread`/`wait_for_message` return list; `cli.py:cmd_wait` iterates list). Verified with isolated unit tests (`HOME` overridden to scratch dir, touching no production `~/.agent-peer`) and simulation using a read-only copy of real production inbox (`antigravity`, 42 historical messages) — first call after "restart" proven not to replay old history (cursor defaults to current time, not 0).

Note: `agent-peer` installation on this machine is an **editable install** (`uv tool install --editable .` → pointing directly to this repo), so source changes automatically activate for subsequent `agent-peer` calls without manual reinstall. Running `listen`/`wait` processes started prior to this edit are unaffected until restarted (Python already loaded old code into memory).

**Settle window (delay before return, to merge burst messages) considered but decided AGAINST** — user usage pattern does not involve sub-second message bursts, and existing backlog-merge (`get_unread` takes all messages from cursor to current point in a single snapshot) is sufficient for the primary scenario (accumulating while busy working). Additional delay would only increase wakeup latency without real benefit in this case.

## Follow-up: Concurrent `wait` Lock

Discovered via live testing: the agy harness can spawn a new `agent-peer wait` without closing the old invocation for the same session (confirmed via `ps` — 2 `wait --name antigravity-test` processes alive simultaneously, same PPID, different TTY). This is a real race condition: both processes read the same cursor, potentially both catching & returning the same message (duplicate delivery, not lost data).

**Fix:** `cmd_wait` (`cli.py`) now acquires a non-blocking exclusive lock (`fcntl.flock`, `LOCK_EX | LOCK_NB`) on `~/.agent-peer/locks/<session>.lock` before beginning polling. If the lock is already held by another `wait` process for the same session → fail fast (`exit 1`, clear message to stderr), rather than silently polling in parallel. Per-session lock (different names do not block each other), and automatically released by the OS when process exits/crashes/`kill`ed — adds no new stale-file risk.

Verified: process B is rejected instantly while process A is alive, process A is undisturbed, lock releases automatically after A dies (including via `kill`, not just normal exit) so subsequent process C can run again, and sessions with different names do not block each other.

## Related Findings (Not Part of This Plan, Recorded From Research)

`agent-peer list` can display listener sessions that were "closed" in the UI but remain ALIVE=yes. Full analysis + fix plan moved to [`docs/stale-listener-detection.md`](./stale-listener-detection.md) — in short: not PID-reuse, process is genuinely alive (verified via `procStart` matching `ps lstart`), just never received a signal triggering its `cleanup()`. Temporary manual fix: `kill <pid>`.
