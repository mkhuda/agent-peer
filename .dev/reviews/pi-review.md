# Review & Audit of `agent-peer` + `SKILL.md` from the pi Harness Perspective

**Author:** pi agent (session `pi-98661`)
**Date:** 17 September 2026
**Session Context:** Audit of pi's bash tool timeout (= SIGKILL of the process tree), correction of the "background task" framing in SKILL.md, 3-harness relay test (agy → pi → opencode → agy), and source review of `protocol.py`, `inbox.py`, `listener.py`, `registry.py`, `sender.py`, `cli.py` plus a re-read of `docs/` and `.dev/HANDOFF.md`.

---

## 1. Executive Summary

From the pi side, `agent-peer` proved reliable for multi-harness collaboration: auto-wakeup via `wait` (without timeout) works end-to-end, the 3-harness relay completed without polling, and auto-naming (`pi-98661`) is stable. The earlier HANDOFF findings (cursor/backlog, anti-race lock, auth fix, ENGINE/cwd hardcode) are all verifiable in the current source.

However, from the pi harness point of view there are several things **not yet covered** by the agy review / opencode-report / HANDOFF:

- **Verified UX bug:** the `new-msg` status in the session json is never reset back to `idle` after a message is read → `agent-peer list` forever shows `new-msg` for any session that has ever received a message, misleading the "who hasn't read their messages" detection.
- **"Delivered" semantic ambiguity:** `agent-peer send` prints "✅ Delivered" when a message reaches the *listener socket*, NOT when the agent has read it. There is no delivery receipt / read-ack. In this session the foreman repeatedly concluded "the message has been read" from "Delivered" — a wrong assumption that can disrupt coordination.
- **`uds:` sender label = PID, not session name:** messages from a registered agy session arrive as `From: uds:/tmp/cc-socks/52285.sock` (label `52285`), not `antigravity-test`. Less useful for debugging/notifications.
- **There is no test suite at all in the repo** — every validation is live-manual. This pattern already proved fragile: several bugs (ENGINE hardcode, cwd hardcode, auth-bypass) slipped through until found in live sessions.

---

## 2. Verified Findings (Empirical + Source)

### 2.1 `new-msg` Status Is Never Reset to `idle` — UX Bug
- **Location:** `listener.py:244` (sets `new-msg` when a message arrives) vs `inbox.py` (`wait_for_message` never touches the session json at all).
- **Empirical evidence:** `agent-peer list` shows `pi-98661 ... new-msg`, `antigravity-test ... new-msg`, `opencode-15297 ... new-msg` even though ALL messages were already read via `wait` many times. A `grep "idle\|new-msg"` across the codebase confirms: `idle` is only written once in `setup()` (`listener.py:121`); no code writes `idle` afterwards.
- **Impact:** the STATUS column of `agent-peer list` — which reads as "has unread messages" — becomes permanently `new-msg` after the first message. Anyone using this column to decide "should I read my inbox" gets misled.
- **Recommendation:** reset `status: "idle"` (+ `statusUpdatedAt`) when the cursor advances in `wait_for_message` / `clear_inbox`, or when `wait` returns. Cleanest: `wait_for_message` already knows which session's cursor advanced — update the session json in `~/.claude/sessions/<pid>.json` at the same time.

### 2.2 "Delivered" ≠ "Read" — a Semantics Ambiguity that's Dangerous for Coordination
- **Location:** `sender.py:send_message` returns `elapsed_ms` after a socket send; `cli.py:cmd_send` prints `✅ Delivered in <ms>ms to <target>`.
- **Fact:** delivery means the frame was successfully written to the target listener socket and landed in its inbox. There is no ack that "the agent read it" (an agent reads via `wait`, which advances its cursor).
- **Empirical evidence from this session:** the foreman (claude-test) said "Both arrived, thanks" based on my "✅ Delivered" send output — but that only verified arrival at the listener, not that it was read. In a multi-hop relay (agy → pi → opencode → agy), if one hop stalls before `wait`, "Delivered" on the previous hop creates a false impression that the message was processed.
- **Recommendation (minimal):** change the text to `✅ Delivered to listener` and add a clarifying line (e.g. "this means received by <name>'s listener, not yet read"). Stronger option: add a per-message `read` status (the agent that `wait`s the message marks it `read: true`), and have `agent-peer status`/`list` show "unread" vs "read" per session. This is the turnkey way to detect "which session hasn't processed its instruction".

### 2.3 `uds:` Sender Label = PID, Not Session Name
- **Location:** `protocol.py:format_user_frame` → `origin_from = f"uds:{from_sock}"`; `listener.py:sender_label` parses `uds:` → `os.path.basename(...).replace(".sock","")` = a **bare PID**.
- **Empirical evidence:** a message from `antigravity-test` (which listens, so it has a socket) was recorded as `From: uds:/tmp/cc-socks/52285.sock`; meanwhile a message from `claude-test` (which does NOT listen, so no socket) shows `From: claude-test`. So registered sessions show a PID and unregistered ones show a name — **inverted from what's most informative**.
- **Impact:** macOS notifications, titles in `agent-peer list`, and logs show a bare number; debugging who actually sent becomes harder.
- **Recommendation:** in `handle_client`/`process_incoming_frame`, resolve `uds:*` to the session name via `resolve_session(pid)` (registry) when present; fall back to the PID when not found.

---

## 3. Source-Only Findings (Not Yet Verified Live, but Readable in the Code)

### 3.1 `peerFeatures` Advertises `notify_idle` Which Is Not Implemented + Hardcoded `version`
- `listener.py:105-111`: `"peerFeatures": ["notify_idle", "reply_across_default_dirs", "artifact_yield"]`; `version` is hardcoded to `"2.1.270"` (`listener.py:112`).
- HANDOFF itself notes `notify_idle` is "a cosmetic field, not implemented". Claiming non-existent features to protocol consumers (real Claude Code reads `peerFeatures`) is risky: a peer agent could assume this session supports `notify_idle`/`artifact_yield` and rely on it.
- **Recommendation:** drop `notify_idle` (or implement it), make `version` dynamic from the package (`__version__`), or at minimum comment why it's hardcoded if it's intentional for compatibility.

### 3.2 `detect_harness_identity` Does Not Skip `sshd`/`tmux`/`screen`
- `_GENERIC_PROC_NAMES` (`protocol.py`) contains `zsh/bash/sh/dash/tcsh/csh/ksh/fish/login/env/sudo/su/node/uv/uvx/python/python3` — but NOT `sshd`, `tmux`, `screen`, sshd children, or container runtimes.
- A realistic pi scenario: pi running from a laptop over SSH (chain `pi ← bash ← sshd`) or inside a tmux session (`pi ← zsh ← tmux`). The auto-name would become `sshd-<pid>` / `tmux-<pid>` — a wrong and unstable identity for collaboration.
- **Recommendation:** add `sshd`, `tmux`, `screen`, `ssh`, `mosh-server`, `containerd-shim`, etc. to the skip list; add a unit test for these chains so it doesn't regress.

### 3.3 `client.settimeout(5.0)` Can Drop Large Fragmented Frames
- `listener.py:handle_client`: one 5s timeout for the whole connection. A large JSON message (> socket buffer) sent with > 5s gaps between fragments will hit the timeout → connection closed → message dropped.
- Fine for typical small frames, but `agent-peer` itself advises "don't send large diffs" — there's no frame-size enforcement; anyone sending an MB-scale payload is at risk.
- **Recommendation:** a per-`recv` timeout (reset the timer on every chunk) + document a frame-size limit; or cap frame size at the receiver (drop + log when > N MB).

### 3.4 `resolve_session` Partial-Match Can Send to a Different Session Than Intended
- `registry.py`: when there's no exact match it falls back to `target_lower in name`. `agent-peer send pi ...` will match `pi-98661` — which is fine when it's the only match, but it happens without confirmation. Low risk, but real in an ecosystem with many `pi-*`/`claude-*` sessions.
- Ambiguity is already guarded (errors when > 1 match). The remaining edge is **exactly-one-but-wrong**. Recommendation: prefer exact match when present; if matching is partial and there are > 5 sessions, echo the resolved name in the output for confirmation.

### 3.5 Lock Files Are Never Unlinked (Junk Accumulates)
- `cmd_wait` (`cli.py`) creates `~/.agent-peer/locks/<session>.lock` and releases the `flock` when done — but never `os.unlink`s the file. Functionally safe (flock is released by the OS when a process dies) but the `locks/` directory accumulates a permanent file per session. Minor; recommendation: `os.unlink(lock_path)` in the `finally` block.

### 3.6 `_read_cursor` Falls Back to `time.time()` on Corrupt Cursor — Backlog Can Be "Lost"
- `inbox.py:_read_cursor`: if the cursor file can't be parsed, it falls back to `return time.time()` → the cursor jumps to "now"; ALL unread messages before the corruption are permanently skipped (treated as old).
- Safer: fall back to `0` (assume nothing was read — never skip messages) with a stderr warning. This is consistent with the "never lose a message" spirit already applied to backlog-merge.

---

## 4. Assessment of the pi SKILL.md (Current Corrected Version)

What is already good (result of this session's corrections, verified against source):
- `listen` = detach at the shell level with `&`, because the process never exits on its own (called synchronously it would freeze the turn forever) ✓
- `wait` = the synchronous LAST tool call of the turn, without agent-peer's `--timeout` NOR the bash tool's `timeout` (two different things that were initially conflated) ✓
- pi bash tool explanation: synchronous, `timeout` = SIGKILL of the process tree, no auto-relaunch ✓

Gaps still remaining in the pi SKILL.md:

1. **`agent-peer listen > log 2>&1 &` without `nohup`/`disown` is not verified to survive in every harness.** What PROVED to survive in this session was `nohup agent-peer listen > /tmp/ap-listen.log 2>&1 & disown`. In pi's bash tool the child is detached (`detached: true`) and my listener (PID 512) is still alive — but that was with the extra `nohup`. **Recommendation:** add `nohup` + `disown` to the SKILL.md example (safe for pi/opencode/any bash, harmless if another harness has real background tasks) plus a verification line: "confirm the listener survives the tool call (`agent-peer list` shows you) before relying on it".
2. **No sentence about the meaning of "Delivered"** (see 2.2) — add: "Receiving the delivery confirmation does NOT mean the peer has read your message; it means their listener wrote it to their inbox."
3. **No guidance for subagents/parallel tasks** (also found by agy): if a session runs subagents/parallel tasks that need independent communication, they MUST use `--name <unique>` — otherwise all subagents inherit the parent name (`pi-<pid>`) and collide on the `wait` lock plus the inbox mixes. The pi SKILL.md could add this single line (agy already recommends the same for its harness).
4. **No explicit verification step after `listen`:** "confirm in `agent-peer list` that your name appears (correct ENGINE, STATUS idle)" — precisely the step that caught the ENGINE/cwd bugs this session. One cheap line that saves future sessions.

---

## 5. Suggested Fix Priority (for the Foreman)

| # | Finding | Severity | Fix |
|---|---|---|---|
| 1 | Stuck `new-msg` status (2.1) | **High (UX)** | Reset status when cursor advances / `wait` returns |
| 2 | "Delivered" ≠ "read" (2.2) | **High (coordination semantics)** | Change the text + optionally add read/unread status |
| 3 | No test suite (exec summary) | **High (engineering)** | Unit tests for protocol/inbox/cursor/registry + an integration smoke test |
| 4 | UDS sender label = PID (2.3) | Medium | Resolve PID → name via registry |
| 5 | Misleading `peerFeatures`/`version` (3.1) | Medium | Drop claims for features that don't exist |
| 6 | `sshd`/`tmux` not skipped (3.2) | Medium | Expand `_GENERIC_PROC_NAMES` + tests |
| 7 | SKILL.md: `nohup`/`disown`, Delivered meaning, subagent `--name`, post-listen verification (4) | Medium | Patch the pi SKILL.md |

---

## 6. Conclusion

`agent-peer` is functionally "proven" for pi (auto-wakeup, backlog, lock, 3-harness relay all work in real sessions). The most urgent issues are not in the core messaging flow but in **misleading status semantics** (`new-msg` stuck, "Delivered" misinterpreted as "read") and the **absence of a test suite**, which has let regressions (ENGINE/cwd/auth hardcodes) go unnoticed until live sessions. Both deserve to be fixed before agent-peer is used as the engine for more serious coordination (e.g. task handoffs with many parallel sessions).
