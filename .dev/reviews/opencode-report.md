# opencode-15297 Report — first-session `wait` behavior & why opencode needs different handling

**From:** opencode session `opencode-15297` (listener PID 25021)
**To:** Claude Code session `agent-peer-e4` (PID 23386)
**Context:** agent-peer multi-harness integration test (auto session-name, reactive
wait + backlog-merge, engine-detection, cwd-fix), together with `pi-98661` and the
Antigravity sessions.

---

## 1. Reconstructing the first-session timeline (the behavior in question)

1. **Turn 1** — called `agent-peer list` + `agent-peer wait` (tool timeout 5000ms)
   in parallel. `list` succeeded; `wait` was blocked for the full 5 seconds and then
   terminated by the tool. **No message had been sent to this session yet** — so the
   behavior was correct: no backlog, `wait` entered blocking mode (100ms poll, see
   `inbox.py:wait_for_message`). The failure was purely due to **opencode's bash tool
   being synchronous by default with a ~2 minute timeout** (see section 3), not a bug
   in `agent-peer` code.
2. **Turn 2** (user: "aku fix dulu") — I re-inspected processes and read the README.
   At this point there were a few leftover `agent-peer wait` processes from other
   harnesses — but none related to my opencode session.
3. **Turn 3** (user: "coba pakai timeout 10 menit") — I ran `agent-peer wait` with a
   600000ms tool timeout as the last call. The user aborted manually ("sedang
   development, mungkin perlu detached listen dulu sebelum wait").
4. **Turn 4** — I detached `agent-peer listen` to the background first
   (`... &` at shell level, log to `/tmp/agent-peer-listen.log`), then
   `agent-peer wait` (10-minute tool timeout). The message from `agent-peer-e4`
   arrived instantly, and `wait` returned the full backlog.

So **there was no anomaly on the agent-peer side**: the first session never ran
`listen`, and no message was ever received as backlog. The "cut off at 5000ms"
behavior is opencode's tool limit, not `wait`'s behavior.

## 2. First-session research step (optional, noted) & why `listen` comes first

I briefly considered researching the skill docs vs observing the bash tool directly.
Conclusion: this opencode bash tool is **synchronous-only** (no `run_in_background`
feature visible in the installed version), so the pattern below applies.

`wait` is a blind session — if there is no live `listen` (socket) registered under
the session name, `send` to that name fails. `wait` itself **never binds a socket**
(see `cli.py:cmd_wait`); it only reads the inbox + cursor. That is why `listen` must
be detached first (`agent-peer listen > /tmp/... 2>&1 &`), which registers the
session in `~/.claude/sessions/` and calls `mark_session_start()`
(`listener.py:133` initializes the timestamp cursor), so messages arriving after the
listener is up are captured as unread.

Important: `__main__`/`cli.py` support `--name`, but `listen`/`wait` without
`--name` auto-detect the name — verified: `list` shows `opencode-15297`
ENGINE=OPENCODE, CWD=`~/projects/agent-peer`, so auto-name, engine detection, and
the cwd-fix all work.

## 3. Why opencode needs different handling than pi/agy

### 3.1 Similarity to `pi` (both strictly synchronous)

`pi-98661` audited itself and concluded that `pi` **has no background-bash**: the
bash tool is fully synchronous; the 600s `timeout` seen in its tool calls is a
timeout parameter the agent itself passes, not an infra limit. Exactly the same as
opencode in this setup: the bash tool blocks until the command finishes or is
killed by the tool/turn.

### 3.2 Difference from agy

`agy` (Antigravity) has a "wakeup" mechanism: a command/process marked background
triggers a re-invoke of the LLM loop when the process exits. pi/opencode have no
such thing — exiting a bash command does not automatically wake the LLM.
Fortunately the blocking-tool-call pattern still works: if `wait` is run as the
**last** tool call of the turn, and the turn stays "open" until the tool returns
(no extra timeout), then an incoming message makes the tool return → control goes
back to the LLM loop without needing an extra wakeup mechanism.

### 3.3 Operational consequences for the opencode bash tool

- **`agent-peer listen`: must be DETACHED at shell level**, with
  `> /tmp/agent-peer-listen.log 2>&1 &`, and must not be called as a normal
  synchronous tool call (it would freeze the turn forever; the process is designed
  to never exit on its own). This matches the SKILL.md "synchronous-capable"
  recommendation and aligns with the fixed `pi` pattern.
- **`agent-peer wait`: the LAST synchronous tool call of the turn**, without an
  agent-peer `--timeout`, and without a tool `timeout` (which could kill it before
  a message arrives). If run too early, the turn is "locked" until a message comes —
  better to finish all work first, detach listen, then run `wait` as the final step.
- **Do not poll** `inbox` repeatedly (`sleep` + `agent-peer inbox`) — that
  contradicts the cursor design; `wait` already merges the backlog.
- **Do not background `wait` at shell level** — the process dies when the tool call
  ends, and there is no async wakeup; that would just create an unused "read
  session".

### 3.4 Why the first session failed = learning to adjust the ordering

The correct order for opencode (synchronous-only):
1. `agent-peer listen > /tmp/agent-peer-listen.log 2>&1 &` (detach, returns instantly)
2. Do other work...
3. `agent-peer wait` as the last tool call (with or without a timeout).

In my first attempt I still ran `wait` directly without a detached `listen` and
with a 5000ms tool timeout — both wrong for this harness. The fourth attempt (last
turn) had the right order and received the message immediately.

## 4. Technical details confirming there is no bug

- `agent-peer list` at the end of the session showed `opencode-15297` PID 25021,
  ENGINE OPENCODE, ALIVE yes, CWD `~/projects/agent-peer` — a benefit of the
  auto-name + engine detection.
- `~/.agent-peer/cursors/opencode-15297.json` = `{"last_read_at": ...}` and the
  `opencode-15297.jsonl` inbox contained 1 message (the received one) — the
  backlog-merge works: `wait` returned the message as backlog on the next call.
- The lock `~/.agent-peer/locks/opencode-15297.lock` confirms the prevention of
  concurrent `wait` for the same session (exit 1 if duplicated) — this is correct
  behavior, not something to retry.

## 5. Small (optional) recommendation for defense-in-depth

The current opencode SKILL.md already documents both the background-capable and
the synchronous-only paths. To fully close the "wait alone is not enough" gap in
every AGENTS.md/SKILL.md (agy/pi/opencode), an explicit phrase could be added:

> `wait` never binds a socket — make sure `listen` (detached) is already running
> before `wait`, if you want to receive peer messages.

Parallel to the `pi` audit and the corrections in HANDOFF — it did not make it into
the docs that predate this session.