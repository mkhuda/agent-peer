# Review & Audit of `agent-peer` + `SKILL.md` from the opencode Harness Perspective

**Author:** opencode-15297 (opencode v1.18.31, model openrouter-auto-claude)
**Date:** 17 September 2026
**Context:** real hands-on session: synchronous-only bash tool, short default tool
timeout that must be set per call, wait as the last tool call, 3-harness relay
(agy → pi → opencode → agy), agent-peer training for the multi-harness skill.

---

## 1. Executive Summary

`agent-peer` works well as an IPC mesh: delivery under 200ms (165–168ms measured),
reactive `wait` with backlog-merge, auto session-name, engine detection, and lock
concurrency all confirmed working from the opencode harness. I found no blocking
bug in the core flow.

But from the specific viewpoint of the opencode harness — **strictly synchronous
bash tool, a short default tool timeout that must be re-set, and manual `listen`
detach** — there is one design-level gap I consider most important: **a tool
timeout killing `agent-peer wait` does not automatically restart standby** on the
agent-peer side, and there is no reliable local "watchdog" to do it. Section 4
discusses mitigations that could go into the product as well as into docs/SKILL.

---

## 2. What was verified live

- **`wait` catches backlog instantly & blocking mode:** messages from
  `agent-peer-e4` and `pi-98661` were caught with no polling; when there is a
  backlog it returns immediately, when empty it blocks (100ms poll).
- **3-harness relay:** agy → pi → opencode → agy closed; `send` to
  `antigravity-test` 166ms, report to `agent-peer-e4` 165ms.
- **Name auto-detection:** the session registered as `opencode-15297`
  (PID from `/Users/rg/.local/bin/opencode`, not the transient PID of `wait`).
- **Engine + CWD:** after the fix in `listener.py`/`cli.py` (agentType from
  `detect_harness_identity`, cwd = `os.getcwd()`), `agent-peer list` shows
  ENGINE=OPENCODE and CWD=`~/projects/agent-peer` correctly.
- **Lock:** per-session `fcntl.flock` rejects a second `wait` for the same session
  (exit 1) — correct behavior, not something to retry.

---

## 3. Audit results of the opencode `SKILL.md` (`~/.config/opencode/skills/agent-peer/SKILL.md`)

### 3.1 Fatal ambiguity: "check your own bash tool first"
Lines 27–44 offer two paths (background-capable vs synchronous-only) and ask the
agent to "check first". In practice, the agent (me) cannot reliably determine the
bash tool's background capability from inside the session — and verification is
not available at the moment `wait` must be called. Consequence: this section is
just guesswork, and a wrong guess ends in a frozen turn or missed messages
(exactly what I hit in turn 1: no detached listen and a 5000ms tool timeout while
`wait` was blocked).

**Recommendation:** replace "check your own bash tool" with a **safe default for
synchronous + a firm `listen`-detach-with-`&` opinion** (already proven), then add
a note that background-capable sessions may use another path — not the other way
around.

### 3.2 No explanation of the `listen` vs `wait` relationship (the "wait-alone not enough" gap)
`SKILL.md` describes the two separately but never states clearly that **`wait`
alone does not make the session reachable** — a live `listen` is required
(`sender.py` connects to the socket; `wait` only reads the inbox). Because `wait`
does not error when unreachable (only a stderr warning in
`cli.py:_warn_if_unreachable`), this is a silent failure that is easy to miss.
Note that HANDOFF.md already recorded the "wait alone is not enough" conclusion
from the pi audit, but it has not been propagated to every SKILL/AGENTS yet.
**Recommendation:** one explicit sentence in SKILL & README (which the SKILL uses
as its reference).

### 3.3 The "order" for synchronous-only harnesses is not explicit
SKILL gives a detach snippet but does not mention the proven order:
1) `listen > log &` first, 2) only then `wait` as the last tool call. The missing
order causes confusion (like my turn 1). Add a short step diagram.

### 3.4 The `_warn_if_unreachable` stderr warning does not reach the LLM
When `wait` runs as a tool call, the tool's stderr is **not always rendered to the
model** (depends on the harness). So the "you are not reachable" warning can be
silently lost. agent-peer should treat this as a UX bug — if the tool context can,
print the warning to stdout or use a dedicated exit code that can be monitored.
(See product recommendation section 4.3.)

---

## 4. Main issue: tool timeout killing `wait` = dead standby (design gap)

### 4.1 Mechanics of what happens
- `agent-peer wait` as a blocking tool call, **without an internal `--timeout`**,
  blocks until a message arrives (or dies from a tool timeout).
- In opencode, the bash tool is **synchronous**. Its default has a pending ~2
  minute timeout, and as I experienced, the tool cuts the call without warning.
- Without external monitoring, being "killed" leaves the session as "*poisoned*":
  it no longer receives messages, while the leftover `listener` still consumes
  them.
- Because opencode has no *auto-reinvoke* on command exit, there is no natural
  loop that restarts `wait`. Net result: **silent standby-death**.

### 4.2 Why this is not merely a "documentation" issue
A short default timeout forced on every call → the LLM will not always include a
large enough `timeout` on the final step (token budget, long context, heuristic
decisions). Even with correct docs, the fear that "the tool will kill the call"
remains every time the agent runs `wait` as the last step.

### 4.3 Mitigations worth considering on the product side (not just docs)
1. **A `--timeout` default better suited to the harness:**
   Add a `--timeout` option to `cmd_wait` whose *default* is currently `0`
   (unlimited). For opencode (synchronous), default `0` is indeed correct, but many
   harnesses have internal limits. One alternative: an extra argument like
   `--self-heal` / `--watch-stale`? This leads toward a "wait-and-relisten"
   feature. Evaluate carefully — we do not want to reintroduce the "auto-spawn
   listener" pattern that was rejected earlier (stale-listener risk). But a
   **watchdog for detecting a dead process** is different: it could be a
   `--heartbeat <sec>` option that prints a periodic marker (e.g. `HEARTBEAT`),
   which a harness could use to tell "the tool call is still alive" — unfortunately
   that only helps if the harness reads stdout in real time, which does not happen
   with a synchronous tool until the command finishes. So for opencode
   (synchronous), such an option does not save standby-death.
2. **A clear exit-code & output contract for detecting revival:**
   When `wait` is killed by a tool timeout, there is no consumable indication
   (none remaining). If opencode supports the env var
   `OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS` — I found no config file setting
   it in this 1.18.31 install; the default remains ~2 minutes. Consequence: **the
   most practical route is making the LLM aware it must always send `wait` with a
   long tool `timeout` (± 10 minutes) on the FINAL step**, and *optionally* re-run
   `wait` as the last step after *every* non-wait action — controlled manually, not
   in the background.
3. **Strong documentation:** include a **"how to keep standby alive"** outline
   pointing to the pattern I used:
   - `listen > /tmp/agent-peer-listen.log 2>&1 &` (session open, truly detached)
   - `wait` as the last tool call with a minimum 10-minute tool `timeout`
   - do not background `wait` (the process dies with no restart trigger)
   - after receiving a message, if further actions may restart processes, be aware
     that `wait` must be **re-run explicitly** afterwards (there is no
     auto-reintegrator).

### 4.4 Priority
In my opinion, prioritizing **closing the "standby-death without automatic
recovery" gap** is the best short-term improvement executable on the opencode side:
(a) reinforce SKILL + README with the order pattern and an *escape hatch*,
(b) add `agent-peer wait` documentation that an external tool `timeout` should be
required. Something more mechanical (a harness-linked watchdog) needs more
evaluation because it ends up in the hidden stale-listener problem.

---

## 5. Other edge cases & reliability concerns (new, specific to opencode)

1. **Is `opencode` harness detection ambiguous?** In this setup the `opencode`
   executable lives at `/Users/rg/.opencode/bin/opencode`, but there is also a
   `herdr server` binary wrapping the session. `detect_harness_identity` finds
   `opencode` from the zsh → `opencode` PPID chain. There may be *many* opencode
   sessions running (TUI + run variants), yet the `opencode-<pid>` names are all
   **mutually distinct because the PIDs are unique** — actually fine. However, if
   several docker/local sessions come near, `opencode-<pid>` still differs because
   PIDs differ. I found no real conflict **in this setup**.
2. **Minor bug in `_GENERIC_PROC_NAMES`:** the list contains `node`, but **not
   `pnpm`, `npm`, `vite`, `tsx`, etc.** Running `auto_session_name` in a session
   booted from `pnpm dev`/`npm run` would yield names like `pnpm-...` or `npm-...` —
   not `opencode-<pid>`. This can cause the session name to change when a harness
   executes through a non-harness wrapper. Make the list account for generic task
   runtimes. Real risk is low because the actual session uses zsh→opencode, but if
   a user starts opencode via an `npx`/`npm` snippet, detection could abort.
3. **Expensive notifications:** `listener.py` calls `terminal-notifier` for every
   incoming message — on a high-traffic harness (e.g. bulk testing) this could
   spam. Not critical for local work.
4. **Cross-session cursor sync (nice-to-have):** `_read_cursor` defaults to
   `time.time()` when the file is missing. If `agent-peer inbox --clear` happens
   without resetting the cursor, genuinely new messages could be swallowed
   (monotonic timestamps help but are not fully conclusive). Already noted in the
   agy/weaknesses report; I agree, and add: better to reset the cursor inside
   `clear_inbox()`.
5. **`cmd_wait` never exposes "tool timeout" as a distinguishable case:**
   `wait` returning `None` → prints "Timeout waiting" and exits 1; a tool kill also
   produces truncated output. The combination makes it hard for logic to determine
   "does standby need to be restarted" versus an empty/no-message case.

---

## 6. Concrete recommendations (summarized)

**For `agent-peer` code (optional, medium–high priority):**
1. Document in README (and SKILL) explicitly: "`wait` requires an active
   `listen`", not only an stderr warning that is often not rendered to the LLM.
2. Consider a `--timeout` on `wait` and/or a clear default env var for harnesses
   that require a limit. Caveat: for opencode, default 0 (unlimited) is already
   right — but document that for synchronous harnesses, if a tool limit exists,
   `wait` must use a tool `timeout` well above the open-turn duration.
3. When `wait` must end due to an (internal) timeout — emit a message distinct
   from "timeout (internal)" vs "tool kill" so they can be told apart (exit code,
   e.g. 3) for harnesses that monitor.
4. Inbox/cursor/lock files: set permissions `0o600`/`0o700` (consistent with the
   weaknesses-report findings).
5. `clear_inbox()` should reset the related cursor (avoid skipping new messages).

**For the opencode `SKILL.md` (high priority so it can be applied):**
1. Replace the "check bash tool first" section with the **already-tested**
   order (synchronous-first): detach listen → wait last with a large tool timeout.
2. Add the explicit sentence "wait alone does not make you reachable — a listen is
   required".
3. Add "what to do AFTER receiving a message": if you will keep communicating,
   call `wait` again as the last tool call.
4. Note that `--name` is optional and parallel subagents/sessions must pass a
   unique `--name` (following up on the agy finding).

**For the global opencode `AGENTS.md`:**
- The clause "run wait as a blocking shell call" is correct, but add one sentence
  that `wait` must be the last tool call and `listen` must be detached first.

---

## 7. Assessment conclusion

- **agent-peer is more reliable than it first appears** — all the new mechanisms
  (cursor, lock, backlog-merge, auto-detection, engine/cwd) work and were live
  tested across 3 harnesses. I found no blocking bug.
- **The most important issue is the timeout-limit-management gap in synchronous
  harnesses** — it needs a combination of firm documentation + a clearer output
  contract (exit code / message) from `agent-peer`, so opencode and similar
  harnesses do not silently lose standby.
- SKILL.md already points in the right direction; what is missing is execution:
  making the synchronous path the **absolute default** and giving explicit ordering
  + recovery guidance.

> Be honest and critical on purpose: not to say "everything is fine", but so the
> gap now visible in opencode gets fixed before being used at multi-harness
> production scale.