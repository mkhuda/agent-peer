# agent-peer skill for muse (Meta Muse Code)

## Install

muse discovers user skills at `~/.agents/skills/<name>/SKILL.md` (same
Agent-Skills-standard convention Codex CLI uses - a skill package must be a
directory, a bare file is rejected):

```bash
mkdir -p ~/.agents/skills/agent-peer
cp "$(pwd)/SKILL.md" ~/.agents/skills/agent-peer/SKILL.md
```

Confirmed live: `muse skills validate` reports profile
`agent-skills-common-subset`, `known_fields: [description, name]`,
`diagnostics: []` for this file as-is. Only `--scope user` is offered by
`muse skills install`; no project-level skills convention was found in this
repo (no `.muse/` directory).

**`~/.agents/skills/` is shared with other harnesses** (Codex CLI also scans
it) - if this machine also runs Codex, install *Codex's* version at
`~/.codex/skills/agent-peer/` instead (Codex-exclusive, see
`skills/codex/README.md`) so the two harnesses' genuinely different guidance
(muse must `wait`; Codex must not) never collide at the same shared path. No
muse-exclusive alternative location is known yet - if one turns up, prefer
it the same way.

## Convention notes

(Verified live against a real muse session - `muse-test`, 2026-09-20, full
findings in the session's own review notes - not assumed from docs.)

- **Engine/version string:** muse's process `comm` reports something like
  `MUSE-BIN-1.3.0-R3401.1` - `agent-peer`'s harness auto-detection uses that
  raw string directly as the engine label, so expect a long one in
  `agent-peer list`, not a clean `MUSE`.
- **Approval:** no Codex-style persistent per-command allowlist at the CLI
  surface. Available flags (all per-launch): `--approval-mode
  untrusted|on-request|never` (default `on-request`), `--disable-approval`,
  `--disable-sandbox`, `--yolo`, `--trust-workspace`, `--sandbox-network
  restricted|enabled|proxy-only`. `trust.json` under `~/.config/muse/`
  persists trust per project, not per command pattern. Launch with
  `--approval-mode never` or `--yolo` to avoid repeated prompts for
  `listen`/`wait` for the rest of the session.
- **Sandbox blocks more than `ps`.** Confirmed with a live syscall probe:
  `kill(other_pid, 0)` returns `EPERM` for every process except the caller
  itself (`kill(self, 0)` is fine); `exec /bin/ps` is blocked outright;
  Unix socket bind is `EPERM` even under `/tmp` (plain file writes there are
  fine - the rejection is at the socket syscall level, not the path); writes
  under `~/.agent-peer/`, `~/.claude/`, `~/.config/muse/` are all `EPERM`
  (reads still work). Net effect without escalation: `agent-peer list` shows
  `ALIVE: no` for every session but your own (see the `prune` warning in
  `SKILL.md`), and harness auto-detection falls back to a generic
  `agent-<pid>` name since it can't exec `ps`.
- **`HERDR_PANE_ID`** (and sibling `HERDR_WORKSPACE_ID`/`HERDR_TAB_ID`) is
  injected by a bundled `herdr` skill (Herdr v0.9.0) and stays stable across
  turns in the same pane (confirmed: 3 reads, unchanged). It's a candidate
  fallback identity when `ps` isn't available. Caveat: `herdr`'s own CLI
  (`herdr pane current --current`) talks over a Unix socket
  (`$HERDR_SOCKET_PATH`), which is *also* `EPERM`'d under the sandbox - so
  under sandbox, the env var is the only part of `herdr` that actually
  works. Also check `HERDR_ENV=1` first; the ID is empty outside Herdr.
- **`agent-peer prune` is genuinely dangerous here without escalation.**
  Since every other session shows `ALIVE: no` under the sandbox, an
  unescalated `prune` would try to delete every registration on the machine,
  not just this session's own stale ones - `agent-peer` refuses this
  specific case by default (all sessions dead at once) and needs an explicit
  `--force` to proceed, precisely because of what this sandbox does.
