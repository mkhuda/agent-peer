# agent-peer skill for Codex CLI

## Install

Codex discovers `SKILL.md` files under several locations at once - project
`.agents/skills/<name>/` (walking up to the repo root), user
`~/.agents/skills/<name>/`, admin `/etc/codex/skills/<name>/`, **and**
`$CODEX_HOME/skills/<name>/` (`~/.codex/skills/`, where Codex's own bundled
`skill-installer` places things). Confirmed live: Codex shows the same-named
skill **twice** in `/skills` if it exists in more than one of these at once
- pick exactly one.

**Use `~/.codex/skills/agent-peer/`, not `~/.agents/skills/`,** if this
machine also runs other harnesses that share the `~/.agents/skills/`
convention (opencode, muse) - `~/.codex/skills/` is Codex-exclusive, so
Codex-specific guidance (skip `wait`, use `codex queue`) never collides with
a different harness's version of this same skill name at the shared path:

```bash
mkdir -p ~/.codex/skills/agent-peer
cp "$(pwd)/SKILL.md" ~/.codex/skills/agent-peer/SKILL.md
```

Use a real copy, not a symlink — a live Codex session did not pick up a
symlinked `SKILL.md` until it was replaced with an actual file. Re-run the
`cp` after editing the source to pick up changes.

Project-local install (this repo's own skill, visible only inside it) works
the same way, just under `.agents/skills/agent-peer/` in the repo root.

## Convention notes

(Verified live against a real Codex CLI session — `codex-test`, 2026-09-17 —
not assumed from generic docs.)

- **Discovery locations:** repo `.agents/skills/<name>/SKILL.md` (searched
  upward to the repo root), user `~/.agents/skills/<name>/SKILL.md`, admin
  `/etc/codex/skills/<name>/SKILL.md`, plus system/plugin skills (this
  machine also has `~/.codex/skills/.system`). Same Agent-Skills-standard
  directory-with-`SKILL.md` shape other harnesses (opencode, Copilot CLI) use.
- **Trigger:** both **implicit** (surfaced to the model by description match)
  and **explicit** — `/skills`, or mentioning a skill by name with `$skill-name`.
- **Instruction loading:** Codex reads `AGENTS.md` before starting work — both
  a global override under `~/.codex/` and any project-path `AGENTS.md` files.
  This repo's own `AGENTS.md` is picked up automatically; no separate
  bootstrap step needed for the working-contract rules.
- **Hooks/MCP:** `~/.codex/hooks.json` and `config.toml`'s `hooks = true`
  wire up lifecycle hooks (`SessionStart`, `PreToolUse`, `PostToolUse`, etc.,
  including async ones). Not required for `agent-peer` itself, but relevant
  context if a future skill needs to auto-register on session start instead
  of an explicit `listen` call.
- **Background execution:** unlike pi and opencode, Codex's exec tool
  supports **real long-running background sessions** — `agent-peer listen`
  stays alive and reachable without being manually detached with `&`, and
  `agent-peer wait` was confirmed to block correctly and return the instant
  a peer message arrived (round-tripped live against a Claude Code session).
- **Sandbox:** a default sandbox profile blocks the socket bind (`/tmp/cc-socks/`)
  and the `ps` call used for engine auto-detection until explicitly approved
  — expect one approval prompt per command shape (`agent-peer listen`,
  `agent-peer wait`) on first use, then it's remembered for the session.
- **Native inbound delivery:** Codex has its own inter-session push, `codex
  queue --thread <uuid> --message ...`, built on the Codex App Server.
  `CODEX_THREAD_ID` (and `CODEX_SESSION_ID`, same value) **is** set in the
  process environment as of Codex 0.154.0 — confirmed live by reading `env`
  inside a real session, contradicting an earlier check of `openai/codex`
  issues #8923/#5912 that found it unshipped (likely just landed since).
  `agent-peer listen` reads it automatically, no flag needed;
  `--codex-thread <uuid>` / `$CODEX_THREAD_ID` remain available as an
  explicit override for an older Codex without it. Once registered,
  `agent_peer/sender.py` routes `agent-peer send` to that session through
  `codex queue` instead of the file-based inbox — confirmed live, no `wait`
  involved.
- **Skip `agent-peer wait` here:** it works, but Codex's runtime caps a
  blocking call at roughly 60s and resumes it as a new turn - each resumption
  costs a turn for no work done. `listen` alone gets native delivery for
  free; only use `wait` for a quick one-shot check, never as a standby loop.
