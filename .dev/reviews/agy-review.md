# Review & Audit of `agent-peer` + `SKILL.md` from Google Antigravity Harness (AGY) Perspective

**Author:** Google Antigravity Agent (AGY)  
**Date:** September 17, 2026  
**Session Context:** Real-world evaluation of message delivery, backlog cursor, lock concurrency, auth fixes, multi-harness relay test (AGY -> PI -> OPENCODE -> AGY), and auto-session name detection.

---

## 1. Executive Summary

In a series of live multi-harness tests, `agent-peer` demonstrated excellent core IPC (inter-process communication) functionality between agents:
- Message delivery latency via Unix Domain Sockets was consistently **< 200ms** (~165-174ms).
- The **unread cursor & backlog merging** feature successfully captured messages arriving while the agent was busy executing other tasks without any message loss.
- The exclusive `fcntl.flock` lock successfully prevented duplicate *race conditions* for the same session name.

However, deep evaluation from the **Google Antigravity** harness perspective revealed several **ambiguities in `SKILL.md`**, **edge cases in the auto-detection process**, and **potential concurrency issues with subagents/multi-session setups** that are critical to address.

---

## 2. Ambiguities & Issues in `SKILL.md`

### 2.1 ~~The `/agent-peer intro` Command is Incompatible with Antigravity CLI~~ — WITHDRAWN
* **Initial Claim:** Line 8 stated `Trigger explicitly any time with /agent-peer intro to load this skill`, which was initially deemed incompatible with Antigravity's built-in slash commands.
* **Correction (re-verified in this session):** `/agent-peer` was proven to be fully supported & officially registered once the skill is installed — Antigravity UI integration for custom skill slash commands works seamlessly. Claim 2.1 above is **invalid**, left struck-through (not deleted) as an investigation trail.

### 2.2 Confusion Between Role Alias vs Registered Session Name
* **Issue in SKILL.md:** Section B instructs `agent-peer send <peer-name> "[fyi from antigravity]: ..."` and permits `--sender`.
* **Harness Reality:** When a sender executes `agent-peer send claude-test "..."`, the command fails (`ValueError: Session 'claude-test' not found`) if the receiving listener is registered in `~/.claude/sessions/` with a physical name like `agent-peer-e4`. The `--sender` parameter in the sender frame only alters the `from` string inside the payload, without registering a name alias in the registry.
* **Recommendation:** Clarify in `SKILL.md` that `<peer-name>` **MUST** match the `SESSION NAME` column output by `agent-peer list`, not a functional/role alias of the agent (unless that session was explicitly started with `--name <role>`).

---

## 3. Auto-Detection Edge Cases (`detect_harness_identity`)

### 3.1 Subagent & Parallel Task Lock Collision
* **Auto-Detect Mechanism:** `detect_harness_identity()` traverses up the process tree (`os.getppid()`) looking for the first non-generic process name. For Antigravity, it finds `agy` with the main PID (e.g. `PID 33402`), creating the name `agy-33402`.
* **Issue with Subagent/Child Tasks:**
  If an Antigravity agent spawns a subagent (e.g. via `invoke_subagent` or background worker processes within the same workspace), all subagents inherit the **same** parent `agy` process (`PID 33402`).
  As a result:
  1. All subagents are auto-detected with the **exact same** name (`agy-33402`).
  2. When subagent A and subagent B both call `agent-peer wait`, subagent B fails immediately due to the `fcntl.flock` lock on `~/.agent-peer/locks/agy-33402.lock` ("already running").
  3. Messages intended for a specific subagent get mixed up in `agy-33402`'s inbox.
* **Recommendation:** Add a rule in `SKILL.md` stating that if an agent spawns separate subagents or parallel tasks requiring independent communication, subagents **MUST** specify an explicit name (e.g., `agent-peer wait --name agy-subagent-1`).

### 3.2 Accumulated File Locks & Multi-Name Standby Leak
* **Antigravity Harness Reality:**
  Antigravity manages background processes via `run_command` with `WaitMsBeforeAsync: 1000`. Over a long session, an agent might switch standby names from `--name antigravity-test` to auto-detected `agy-33402`.
* **Effect:** `task-58` continues to hold `antigravity-test.lock`, while `task-84` holds `agy-33402.lock`. Two separate listeners/waiters run concurrently in the background without releasing locks because their session names differ.
* **Recommendation:** `agent-peer` should include an `agent-peer stop` command or automatic lock cleanup if a previous listener under the same parent PID is detected with an old name alias.

---

## 4. Codebase Security & Reliability Evaluation (Technical Findings Summary)

1. **Inbox File Permissions (`~/.agent-peer/inbox.jsonl`):**
   The `~/.agent-peer/` directory and inbox files are created with default umask permissions (`0644`). In a multi-user Unix environment, other local users can read inter-agent message contents. *Fix: Enforce `chmod 0700` on the directory and `0600` on inbox/cursor files.*
2. **Missing `SO_PEERCRED` Socket Verification:**
   `listener.py` verifies the string authentication token, but does not verify the sender's OS socket credentials (`SO_PEERCRED`).
3. **Non-Atomic File Append (`append_inbox`):**
   `append_inbox()` performs message appending without `fcntl.flock`. Under high load from multiple socket threads, JSONL writes can suffer line interleaving.
4. **Notification Sanitization (AppleScript / Terminal Notifier):**
   The regex `clean_snippet = re.sub(r'<[^>]+>', '', content)` in `listener.py:216` strips HTML/XML tags. If an agent sends JSX/HTML code snippets (like `<div>`), characters inside those tags get accidentally deleted from the notification display.

---

## 5. Conclusion & SKILL.md Improvement Checklist

| Item | Status | Recommended Action |
|---|---|---|
| Slash Command `/agent-peer` | Withdrawn | Retain custom slash command support documentation in Antigravity UI |
| Peer Name Resolution | Error-prone | Enforce that `<peer-name>` must strictly match `agent-peer list` output |
| Subagent Isolation | High Risk | Document mandatory `--name <subagent-id>` requirement for subagents |
| File & Socket Security | Medium Risk | Enforce `chmod 0600` on inbox/cursors & lock files |
| Atomic File Write | Medium Risk | Implement `fcntl.flock` on `append_inbox()` |
