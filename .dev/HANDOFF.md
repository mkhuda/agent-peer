# HANDOFF

## 2026-09-17 — `wait`/`watch` mechanism analysis, unread cursor plan

**Context:** user actively uses `agent-peer` for Claude Code <-> Antigravity
(agy) communication. This discussion session was pure research + planning,
no code implementation yet.

**`wait` vs `watch` mechanism findings:**
- `wait` (`inbox.py:wait_for_message`) = blocking tool call, the only thing
  that genuinely triggers agy's "auto wakeup" (process exit = control back
  to the LLM loop). But fragile: this process dies every time agy moves to
  another tool call/task, so it has to be manually restarted every time work
  finishes.
- `watch` (`logs.py:show_logs` follow mode) = a background process that
  never exits on its own, stays alive across tasks — good for passive
  visibility, but doesn't prove it triggers wakeup on its own (depends on
  whether the agy harness monitors its stdout as an async notification —
  unconfirmed).
- `notify_idle` in `peerFeatures` (listener.py:103) checked: cosmetic field,
  no implementation anywhere in this codebase reads it.

**Concrete bugs found in `wait_for_message`:**
1. Baseline computed from `len(inbox)` at the moment `wait` is called →
   backlog messages that arrive while `wait` is dead (agy busy working)
   never get automatically "claimed" when `wait` restarts.
2. `return msgs[-1]` only grabs the last message → if several messages pile
   up within one poll cycle (100ms), the rest are permanently lost (the next
   baseline already considers them "old").

**Decision & status:** design in
[`docs/wait-unread-cursor.md`](../docs/wait-unread-cursor.md) — per-session
timestamp-based cursor (`~/.agent-peer/cursors/<name>.json`), auto-advances
when `wait` is called (no explicit "mark as read" needed from the agent),
`wait` returns a list instead of 1 message. **Implemented**
(`protocol.py`, `inbox.py`, `cli.py`) and verified via isolated tests (`HOME`
override, plus a simulation using a read-only copy of the real production
antigravity inbox with 42 messages — first-call-after-restart proven not to
replay old history). Local install is editable, so source changes are
automatically live for the next `agent-peer` invocation; `listen`/`wait`
processes already running before an edit only pick up the new code after
being restarted. A settle-window/delay before returning was considered then
decided against (user's usage pattern isn't sub-second bursts, the existing
backlog-merge is already enough).

**Separate finding (operational, not a code bug):** `agent-peer list` shows
`antigravity-2` (PID 71277) as ALIVE=yes even though the user thought they'd
closed that session. Verified not PID-reuse (procStart matches `ps lstart`)
— the `agent-peer listen` process is genuinely still alive, it just never
received SIGTERM/SIGINT when the UI session was closed, so `cleanup()` in
`listener.py` never got called. **Correction:** initially suspected this
could be detected via detached TTY (`??`) — turned out wrong, the actively-
used `antigravity` process (72769) has an identical TTY/PPID/STAT (`??`,
ppid=1/launchd, `S`). There's no OS-level signal that distinguishes stale
from active. Fix plan (based on duplicate-name-group detection + recency,
plus a `stop` command using SIGTERM) is in
[`docs/stale-listener-detection.md`](../docs/stale-listener-detection.md).
Temporary manual fix: `kill <pid>`.

**Dead end:** briefly suspected there was an active push/interrupt mechanism
from `agent-peer` to the agy process for wakeup — investigated via a fork
agent, found nothing. No signal/callback of any kind exists; everything
relies on the "blocking tool call" pattern that agy itself initiates, not
any wiring on the `agent-peer` side.

**Live end-to-end verification (real agy session, `antigravity-test` PID
52285):** sent 2 messages (5 second gap) while agy was busy with another
task. Proven by comparing cursor timestamp vs message `received_at` (exact
match in both cases): message 1 triggered an instant wakeup while `wait` was
blocking; message 2 piled up while busy then got auto-consumed when `wait`
was called again after the task finished. The cursor/backlog mechanism
proven to work under real conditions, not just isolated unit tests.

**Follow-up found directly from the live session (`antigravity-test`):**
`ps` confirmed 2 `agent-peer wait --name antigravity-test` processes alive
at the same time (same PPID, different TTY) — the agy harness spawns a new
`wait` without closing the old one. Real race: both processes could
potentially catch the same message (duplicate, not lost). **Fix
implemented:** `cmd_wait` takes a non-blocking exclusive per-session lock
(`fcntl.flock` on `~/.agent-peer/locks/<session>.lock`) — a second
invocation for the same session fails immediately (`exit 1` + a clear
message) instead of silently racing. The lock is released automatically by
the OS when the process dies/crashes. Verified in sandbox: the second
process is rejected instantly, the first process is undisturbed, the lock
releases after the first process dies (including via `kill`), sessions with
different names don't block each other. Details in
[`docs/wait-unread-cursor.md`](../docs/wait-unread-cursor.md).

**Additional fixes done this session (outside the original plan, found/
requested as the session went on):**
- **Auth-bypass in `listener.py`** — found while dissecting agy's report
  (worse than their finding 1.2): the `authenticated` variable was computed
  but never checked before `process_incoming_frame` was called, so the auth
  token was never enforced at all. Fix: add `if not authenticated: continue`
  before `user`/`control` frames get processed. Tested (auth-gate test):
  frame without auth rejected, wrong token rejected, correct token accepted
  — all PASS.
- **Notification click opens Script Editor instead of the relevant app** —
  root cause is macOS behavior (notifications via `osascript` always get
  attributed to Script Editor). Fix: switch to
  `terminal-notifier -activate com.googlecode.iterm2` (falls back to
  `osascript` if `terminal-notifier` isn't available). User confirmed
  clicking the notification now opens iTerm. Details in
  [`docs/notification-click-target.md`](../docs/notification-click-target.md).

**Multi-harness generalization (`agy`/`pi`/`opencode`/etc.) — auto-detected
session names:** the user wants `agent-peer` to stop being Antigravity-only.
Briefly tried detecting harness identity via env vars (grepping strings in
the `agy`/`pi`/`opencode` binaries) — inconclusive, lots of noise, no clear
identity env var. **Approach that proved to work:** walk up the parent
process chain (`os.getppid()` → `ps -o comm=`), skip generic shell/
interpreter names (`zsh`, `bash`, `python3`, etc.), use the first
distinctive process name found + that process's PID (not `agent-peer`'s own
PID, since `wait` is called repeatedly with a different PID each time — a
stable identity is needed, and the harness process itself is stable for the
life of the session). Validated directly against real data from this
session: `zsh` (PID 79203) → `claude` (PID 23386), `agy` (PID 33402) was
also already confirmed from earlier data. Implemented in
`protocol.py:detect_harness_identity`/`auto_session_name`, used lazily
(only computed when `--name`/`--sender` and `$AGENT_PEER_NAME` are both
empty) in `cmd_send`/`cmd_listen`/`cmd_wait` — other commands (`list`,
`status`, etc.) incur zero overhead from this (tested, `list` stays
~0.05s). Agreed scope: every command (`wait`, `listen`, `send`), and `wait`
with no name now reads the per-harness inbox (auto-named) instead of the
merged global inbox — a deliberate decision, not a regression.

**Additional bug found & fixed while testing auto-name:** the
listen→send→wait-for-the-first-time sequence (before that session had ever
called `wait`) caused an already-arrived message to be **missed** — because
the cursor got initialized to "now" at the moment `wait` was FIRST called
(not when the session actually started), so a message that arrived before
that first `wait` call was treated as "already old". Fix:
`inbox.py:mark_session_start()` is called at the end of
`PeerListener.setup()` — initializes the cursor at the earliest possible
point (before `accept()` could ever process anything), instead of waiting
for the first `wait` call. Sessions already running before this fix remain
safe (still use the old fallback in `_read_cursor`, unchanged — the
`test_wait_cursor.py` regression suite still PASSES entirely).

`README.md` updated: the listen section generalized (no longer
Antigravity-only), a new section added about auto-detected names and an
explanation of `wait` (backlog/cursor/lock) that previously wasn't
documented in the README at all.

**Another bug found by the user:** `listener.py` hardcoded
`self.cwd = cwd or os.path.expanduser("~/projects")` — so the CWD column in
`agent-peer list` always showed `~/projects` regardless of the actual
directory `agent-peer listen` was run from. Fix: switched to `os.getcwd()`.
This is in core code (shared by every harness), so it automatically applies
to agy/pi/opencode at once, no per-harness work needed. Tested in sandbox
(cwd now correctly points to the real working directory). Live sessions
that were already registered wrong (`antigravity-test` PID 52285, `pi-98661`
PID 512) were manually patched to `/Users/rg/projects/agent-peer` to match
their actual current working context.

**Bug found by the user during a live `pi` test:** `listener.py` hardcoded
`"agentType": "AGY"` in the session json — a leftover from when this project
was still Antigravity-only. The `pi` session (`pi-98661`, PID 512) was
registered with the ENGINE column wrongly showing "AGY". Fix:
`PeerListener.__init__` accepts an optional `agent_type` (default fallback
`"AGENT"` if unknown), `cli.py:cmd_listen` detects the engine via
`detect_harness_identity()` (independent of the session name — even if
`--name` is given manually, the engine is still detected from the process
tree) and passes it to `PeerListener`. Tested in sandbox: new sessions now
correctly show `CLAUDE`/`PI`/etc. matching their parent process. The
`pi-98661` session json that was already wrong (started before the fix) was
manually patched (`agentType: "PI"`) — the listener process itself didn't
need restarting since the file is only read passively by `agent-peer list`,
not held under a continuous lock by the listener.

**Root cause of opencode only running `wait` without `listen`:** asked
`pi-98661` to audit this — the answer was sharp. Confirmed: `pi` itself
deliberately runs `listen` first (per its SKILL.md), BUT it identified a
real gap in the `SKILL.md`: there was never an explicit sentence saying
"wait alone isn't enough to be reachable — without a live listen, `send` to
your name will fail, and `wait` itself won't error even if you're
unreachable" — a silent failure mode that's easy to miss because `wait`
still "succeeds" (blocks normally) even though that session actually can't
be reached from outside.

**Briefly tried a radical fix (auto-spawn `listen` from inside `wait`), then
the user rejected it** — reason: a subprocess spawned automatically and
fully detached (`start_new_session=True`) risks silently piling up listeners
unnoticed, EXACTLY the "stale listener" problem that was already hard-won
diagnosed in [[stale-listener-detection]]. **Replaced with a non-invasive
guard:** `cmd_wait` now checks `resolve_session(session)` first — if it
fails, print a clear warning to stderr ("no listener running... run
agent-peer listen if you want to be reachable") then still proceed with the
wait as usual (not a hard refusal, since reading an already-piled-up backlog
without a live listener is still a valid use case). No new process is ever
created — it just informs, the decision stays with the agent/user. Tested:
warning appears exactly when unreachable, silent when already reachable, no
new listener appears in either case.

**Final decision on documentation:** considered then decided NOT to
propagate this further into SKILL.md/AGENTS.md — the runtime warning in
`wait` already closes the most dangerous gap (pi's point 2) more reliably
than static documentation (self-documenting at exactly the right moment,
doesn't depend on the agent reading the skill carefully).

**Four-way review collaboration (agy, pi, opencode, Claude) + handwalk task
system:** initialized `.dev/CHARTER.md` (M1 goal: "use agent-peer without
worrying about hidden bugs", gate: run `agent-peer list`+`wait` once more
and nothing looks off), a per-path ownership map across the 4 sessions. Out
of ~20 findings in `.dev/reviews/*.md`, triaged into 10 candidate tasks; the
3 highest-priority ones (tasks 0001-0003) were done directly (not via
worktree, per the user's instruction — worktree deferred):

- **0001** chmod 0600 on inbox/cursor/lock files + 0700 on
  `~/.agent-peer/*` directories (not `SOCKET_DIR`/`SESSIONS_DIR`, which
  belong to Claude Code — deliberately left untouched).
- **0002** cursor safety: (a) `_read_cursor` on corruption now falls back
  to `0` (replay everything) instead of `time.time()` (silently swallowing)
  + a stderr warning; (b) `clear_inbox()` resets the cursor. **A regression
  was found while testing this myself**: the initial fix DELETED the cursor
  file on clear — that reopened the exact gap `mark_session_start` had
  already closed (a message arriving between clear and the next wait would
  get swallowed again). Fixed: the cursor is now **rewritten to the
  moment-of-clear**, not deleted — exactly the same pattern as
  `mark_session_start`.
- **0003** the `new-msg` status in the session json now gets reset to
  `idle` (via `_reset_status_idle` in `cmd_wait`, best-effort through
  `resolve_session`) every time `wait` successfully gets a message — a bug
  `pi` found (status stuck forever after the first message, no code ever
  wrote `idle` back).

All tested in sandbox (`test_wait_cursor.py`/`test_auth_gate.py` regressions
still PASS + new scenarios), **not yet hand-walked by the user** (handwalk
skill rule: a task can only be closed once the owner themself runs its
acceptance sentence on a real machine and writes down what happened). Task
files: `docs/tasks/0001-*.md` through `0003-*.md`. The remaining 7 candidate
tasks (sender-label resolution, "Delivered" wording, `_GENERIC_PROC_NAMES`
expansion, stdout warning, subagent `--name` documentation, test suite,
skills-into-repo) haven't been worked on yet — listed in chat, not yet
turned into formal task files.

**Live `opencode` validation complete, self-diagnosed by that session
itself** (full report in `.dev/reviews/opencode-report.md`, cross-checked
against the real cursor/inbox/lock files — all accurate). Root cause
exactly as predicted: opencode's bash tool is synchronous, default TOOL
timeout of 5 seconds (stricter than the 2 minutes guessed from initial web
research), plus the first session hadn't run `listen` first. Once the order
was right (`listen` detached → other work → `wait` as the last tool call
with no timeout), every feature (auto-name `opencode-15297`, ENGINE
`OPENCODE`, cwd-fix, backlog-merge, lock) validated identically to agy/pi.
**All three target harnesses (agy, pi, opencode) are now validated
end-to-end**, each with its own independent diagnosis from that agent
itself, not just a claim from this session.

**Important architecture correction from a live `pi` test:** the user asked
about the pattern `agent-peer wait; echo "WAIT-EXIT:$?" (timeout 600s)`
that showed up in a `pi-98661` tool call. Asked that session directly, the
answer was detailed and accurate (checked against `dist/core/tools/bash.js`
source by the pi agent itself): **`pi` has no background-bash at all**
(unlike Antigravity, which has `run_command` + a wakeup notification) — its
bash tool is fully synchronous. The 600s wasn't a pi infra limit, it was a
`timeout` parameter the agent itself passed to the tool call (SIGKILLs the
process tree if exceeded). Without a `timeout` on the tool call = genuinely
indefinite blocking.

Implication: the `SKILL.md` I originally wrote for `pi` had the wrong
"background task" framing (copied from the Antigravity version). **Already
fixed:**
- `wait` → stays a synchronous tool call, made the LAST tool call of the
  turn (not "background"), with no `--timeout` on agent-peer NOR the pi
  bash tool's own `timeout` (two different things that got mixed up) — the
  turn stays "open" until a message arrives.
- `listen` → the opposite — it actually MUST be detached manually at the
  shell level (`&`), because calling it synchronously would freeze the turn
  forever (the `listen` process is designed to never exit on its own).
  `AGENTS.md`/`README.md` weren't affected by this issue since they already
  used the phrase "blocking shell call" from the start, never explicitly
  said "background task" like `SKILL.md` did.

**`agent-peer` skill for `opencode`:** researched its convention, turned out
different again from agy/pi — confirmed via WebFetch to
`opencode.ai/docs/skills/`: frontmatter requires `name`+`description`
(optional `license`/`compatibility`/`metadata`), **no auto-load or slash
command** — the skill is invoked explicitly via the tool call
`skill({ name: "agent-peer" })`. Global location:
`~/.config/opencode/skills/<name>/SKILL.md` (there's also a fallback to
`~/.claude/skills/`/`~/.agents/skills/`, deliberately not used so it doesn't
bleed into the real Claude Code install). Created at
`~/.config/opencode/skills/agent-peer/SKILL.md`.

On opencode's bash tool: web research found a default of **synchronous,
2-minute timeout** (env var `OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS`
to change it), plus a `run_in_background` feature (similar to the
Antigravity pattern, auto re-invoke on command exit) — but **not confirmed
whether this feature exists in the installed opencode version (1.18.28)**,
the source was a PR that might be recent. Learning from the earlier wrong
assumption about `pi`, this SKILL.md was DELIBERATELY written without
assuming either way (gives 2 paths: background-capable vs
synchronous-only, asks the agent to check first) — **not yet validated
live**, waiting for the user to open a real `opencode` session to test it
exactly like `agy`/`pi`.

**`agent-peer` skill for `pi`:** injected at
`~/.pi/agent/skills/agent-peer/SKILL.md` (the native location read directly
by pi's core, confirmed from the `skill-store.ts` source — sits alongside
the bundled `web-search` skill). Same frontmatter format
(`name`+`description`), content adapted from the Antigravity SKILL.md but
more concise, matching the `web-search` style. **Not yet validated live**
whether `/agent-peer` in `pi` actually resolves to this file — `pi -p`
failed due to an auth error (`UnrecognizedClientException`) unrelated to
our change. User is testing an interactive `pi` session manually to
confirm.

**Other harnesses' global AGENTS.md:** checked 6 files (`pi`, `opencode`,
Codex, Hermes, Gemini x2) — all empty regarding `agent-peer` (turns out the
instruction for `agy` to use `agent-peer wait --name antigravity` has always
been purely manual, typed by the user every session, not from any permanent
config in `~/.gemini/AGENTS.md`). With the user's approval, a concise
"Cross-agent messaging (agent-peer)" section was added to
`~/.pi/agent/AGENTS.md` and `~/.config/opencode/AGENTS.md` (identical
content, just different auto-detect name examples, `pi-<pid>` vs
`opencode-<pid>`) — covering: `listen`/`wait` without `--name`, `send`,
`list`, pointing to the README for details (no content duplication).
`~/.gemini/GEMINI.md` (agy's actual global config — not
`~/.gemini/AGENTS.md`/`~/.gemini/config/AGENTS.md`, which turned out unused,
containing the "SuperAntigravity Skills" framework instead) also got the
same section added (example name adjusted to `agy-<pid>`). Codex/Hermes
AGENTS.md **not** touched yet — not requested. Whether `pi`/`opencode`
themselves actually have the blocking-tool-call capability needed for
`wait` to be a genuine reactive trigger for them, same as `agy`, **hasn't
been verified** — only assumed to work similarly, no end-to-end test done
yet like the one run against `agy`.

**Found a pre-existing `agent-peer` skill already installed for agy**
(`~/.gemini/antigravity/skills/agent-peer/SKILL.md`) — this is what had been
letting `agy` "know" how to use `agent-peer` on its own without ever being
taught manually in this session (GEMINI.md auto-loads skills based on
description). This skill turned out to be the **original source** of 2 bugs
fixed this session: the old SOP explicitly told it to always use
`--name antigravity` (causing `antigravity-2`/`-3` pileup), and the old
mandatory rule told it to always launch a new `wait` before ending a turn
without ever checking whether the old one was still alive (exactly the
2-wait race scenario found earlier). **Already updated:** removed all
`--name antigravity` hardcoding (replaced with auto-detect), fixed the
`wait` description (now instant backlog-merge, not just "next message"),
added guidance about the new lock-error ("already running" = normal, not
something to retry). Added a "Trigger explicitly `/agent-peer intro`" line
at the top — user confirmed `/agent-peer` in agy really does auto-list the
skill by name.

**Correction:** briefly recommended `--timeout 60` in the mandatory-standby
rule — the user rejected it, correctly. Since `wait` runs as a background
task that triggers wakeup the moment the process exits, a bounded timeout
would make it exit every 60 seconds with nothing to report and need
relaunching — that's polling in disguise, contradicting `wait`'s own
purpose ("never poll"). My lock-safety argument was also wrong — `flock`
releases automatically at the OS level no matter why the process died, it
doesn't need a timeout for that. Sequential sessions waiting on a peer for
minutes/hours are a valid case, let `wait` block without a limit. Already
revised: the mandatory rule is now `agent-peer wait` with no `--timeout`;
the flag stays documented as an option available for other, non-background
uses.

**Additional findings from agy's exploration**
(`docs/agent-peer-weaknesses-report.md`, cross-checked against source —
valid): inbox/cursor files not `chmod 0600` (only the socket & key file are
protected); no `SO_PEERCRED` verification on the socket auth (only string
token matching); race condition from non-atomic writes in `append_inbox`
(no `flock`); cursor doesn't get reset on `agent-peer inbox --clear`; stale
sessions also happen if a listener gets `SIGKILL`/crashes (not just when a
UI session is closed — overlaps with [[stale-listener-detection]] but a
different trigger). None of this list has been worked on yet — pure
findings, waiting on the user's prioritization.

**Bug found by the user while testing `pi` live:** `listener.py` hardcodes
`self.cwd = cwd or os.path.expanduser("~/projects")` — so the CWD column in
`agent-peer list` always shows `~/projects` no matter which directory
`agent-peer listen` was actually run from. Fix: switched to `os.getcwd()`.
This is in core code (shared across every harness), so it automatically
applies to agy/pi/opencode at once, no separate per-harness work needed.
Tested in sandbox (cwd now correctly points to the real working directory).
Live sessions that were already registered wrong (`antigravity-test` PID
52285, `pi-98661` PID 512) were manually patched to
`/Users/rg/projects/agent-peer` to match their current working context.

## 2026-09-17 — Watch accuracy test, git history bootstrap

**`agent-peer watch` accuracy validated live.** User relies on `watch` to
monitor the 3 test harnesses and was worried it might be inaccurate or drop
something. Reviewed `logs.py` and found it had never been specifically
audited by any of the 4 reviewers. Two real caveats found (not bugs in
current normal operation, but worth knowing): (1) a sender/recipient
already dead by the time you view the log shows as `pid-<N>` instead of a
name; (2) `extract_recipient_info`'s "legacy inference" fallback
(`logs.py:144-154`) hardcodes THIS session's own dev PIDs (72769/29258/
71277) as magic numbers — dead weight for anything logged going forward,
but a landmine if those PIDs ever get reused by an unrelated process later
(confirmed PID reuse is a real possibility on macOS). Recommended removing
it; not yet done.

Ran a live 3-way triangle test (agy → opencode, pi → antigravity-test,
opencode → pi, all reporting back to Claude) while capturing
`agent-peer watch --raw` in the background. Cross-checked every reported
message verbatim against the captured JSONL — **100% match, byte-for-byte,
correct sender/recipient attribution, zero loss.** Side finding: `watch`
without a `-s <name>` filter is a genuinely global feed across every
session on the machine — it also picked up unrelated real production
traffic from a different project (`claude-81449`/ottoshare-factory) running
in parallel. Worth using `-s <name>` if the goal is only to watch specific
sessions.

**Gotcha hit while starting the watch process:** mixed `run_in_background`
(the tool's own backgrounding) with a manual `&` plus trailing foreground
commands in the same script — the tool considered the whole invocation
"completed" once the trailing commands finished, even though the actual
`agent-peer watch` child process (backgrounded via `&`) kept running,
reparented to launchd. Not a crash, just confusing: don't mix the two
backgrounding mechanisms in one call — launch the persistent command alone
via `run_in_background`, nothing else in the same invocation.

**Kicked off a 4-way collaborative review** (agy, pi, opencode, Claude), each
independently exploring `agent-peer` from their own harness's perspective
and writing findings to `.dev/reviews/<name>-review.md`. All 4 completed;
cross-confirmed findings across 2+ reviewers were treated as highest
confidence. One direct contradiction surfaced and got resolved: agy's
review initially claimed `/agent-peer` wasn't a real slash command in
Antigravity, then agy itself retracted that after re-verifying live — the
original user claim ("it works") was correct. Corrected in
`agy-review.md` with a strikethrough + note (kept, not deleted, so the
investigation trail survives).

Triaged the ~20 findings from the 4 reviews into 10 candidate tasks (see the
M1 entry above); tasks 0001-0003 were implemented and tested directly.

**Skill templates shipped into the repo** (`skills/<harness>/`) — previously
only lived in each user's global config directory
(`~/.gemini/...`/`~/.pi/...`/`~/.config/opencode/...`), so cloning this repo
gave no way to reconstruct them. Each harness folder ships its current
`SKILL.md` plus a `README.md` covering that harness's own install location,
frontmatter convention, and trigger mechanism — verified against each
harness's own source/docs during this session, not assumed (the three
turned out to genuinely differ: auto-load + `/agent-peer` slash for agy/pi,
explicit `skill()` tool call only for opencode, no slash command at all).

**Git history bootstrapped.** All of this session's work had been sitting
uncommitted the entire time. Committed in 4 logical commits (code / docs+
reviews / handwalk scaffolding / opencode's report), renamed `master` to
`main`, created a private GitHub repo (`mkhuda/agent-peer`) via `gh`, and
pushed. No `Co-Authored-By` line on any commit per the user's standing
instruction.

## Dead ends and rejected approaches (kept so they aren't retried)

- **Auto-spawning `listen` from inside `wait`** when no listener is found —
  rejected by the user: risks silently piling up listeners, the exact
  "stale listener" failure mode already diagnosed separately. Replaced with
  a stderr warning that doesn't create any new process.
- **Detecting a "detached"/orphaned listener via TTY** (`??`) — disproven:
  a currently-in-use listener has an identical TTY/PPID/STAT to a genuinely
  stale one. No OS-level signal distinguishes them.
- **Checking the listener's own parent process for liveness** as a
  detached-session signal — disproven: both listeners were already
  reparented to launchd (PPID=1) immediately on spawn, and there was no
  Antigravity app process visible locally at all to check against.
- **Recommending a bounded `--timeout` for the mandatory background-standby
  pattern** — rejected by the user: turns `wait` into disguised polling for
  any harness that re-invokes on background-task exit, and the lock-safety
  argument for it was wrong (`flock` already releases on process death
  regardless of cause).
