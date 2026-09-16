# Charter

Written by the owner. Read by every session at start. Changes rarely.

## What we are building, and for whom

`agent-peer` is a local IPC mesh so any agent harness (Claude Code, Antigravity/agy,
pi, opencode, others later) can discover, message, and reactively wake up any other
harness session on the same machine — no polling, no per-harness configuration. For
the owner (rg): use agent-peer daily, across harnesses, without worrying about
hidden bugs that only surface once it's actually relied on.

## Current milestone

**M1: Use agent-peer without worrying about hidden bugs.**

Close out the highest-priority findings from the 4 review documents
(`.dev/reviews/*.md`) written 17 Sep 2026 by agy, pi, opencode, and Claude —
especially the ones independently confirmed by 2+ reviewers, and the ones about
security/data correctness (not just documentation convenience).

**Gate:** Owner runs `agent-peer list` and `agent-peer wait` once more after every
M1 task is closed, and nothing looks off.

## Who owns what

Paths, never responsibilities. A path with no owner is nobody's, and changing it is a
request to the owner rather than an edit.

| Path | Owner |
| --- | --- |
| `agent_peer/*.py` (core package) | `agent-peer-e4` |
| `~/.gemini/antigravity/skills/agent-peer/SKILL.md`, `~/.gemini/GEMINI.md` (bagian agent-peer) | `antigravity-test` |
| `~/.pi/agent/skills/agent-peer/SKILL.md`, `~/.pi/agent/AGENTS.md` (bagian agent-peer) | `pi-98661` |
| `~/.config/opencode/skills/agent-peer/SKILL.md`, `~/.config/opencode/AGENTS.md` (bagian agent-peer) | `opencode-15297` |
| `.dev/reviews/<name>-review.md` | the session with the matching name, each owns their own |
| `docs/`, `README.md`, `.dev/CHARTER.md`, `pyproject.toml` | owner only (rg) |

**Not owned by anyone working here:** git operations (commit/push/branch) — owner
only, per rg's global instructions. Killing/terminating other agents' processes —
owner only.

## Decisions that are already made

- **Test policy:** all verification during the 17 Sep 2026 session was done live and
  manually (ad-hoc scripts + real cross-harness tests), there's no automated test
  suite in the repo yet — that's itself one of the M1 findings (see reviews), but
  there's no mandatory-test rule for other tasks until the base suite exists.
- **Don't auto-spawn a new process as a side effect of another command.** Tried once
  (`wait` auto-starting `listen`), the owner rejected it — risk of listeners silently
  piling up. If a new process is needed, it must be explicit from the user/agent, not
  automatic.
- **`wait` doesn't hard-refuse when unreachable** — just a warning (stderr), still
  proceeds (reading an already-piled-up backlog is a valid use case even without a
  live listener).
- **Auto session-name (`detect_harness_identity`) deliberately doesn't guess a
  per-tool env var** — walking the parent-process chain was chosen because it's more
  reliable and needs no cooperation from each harness. Its limitation (subagents
  sharing a parent collide) is known — that's an M1 task.
- **`--timeout` is not a default that gets forced onto anyone** — briefly
  recommended then rejected by the owner for the mandatory-standby pattern
  (background task); leave it per-context, don't hardcode a specific timeout
  recommendation into any skill.
- Editable install (`uv tool install --editable .`) — source changes are
  automatically live on the installed binary with no manual reinstall. A process
  already running before an edit keeps using the old code until it's restarted.
