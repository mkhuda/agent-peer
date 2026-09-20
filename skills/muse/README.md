# agent-peer skill for muse (Meta Muse Code)

## Install

muse discovers user skills at `~/.agents/skills/<name>/SKILL.md` (same
Agent-Skills-standard convention Codex CLI uses — a skill package must be a
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
it) — if this machine also runs Codex, install *Codex's* version at
`~/.codex/skills/agent-peer/` instead (Codex-exclusive, see
`skills/codex/README.md`) so the two harnesses' genuinely different guidance
(muse must `wait`; Codex must not) never collide at the same shared path. No
muse-exclusive alternative location is known yet — if one turns up, prefer
it the same way.

## Convention notes

(Verified live against real muse sessions — `muse-test`/`muse-test2`,
2026-09-20, full findings in `.dev/reviews/muse-review.md` — not assumed
from docs.)

- **Engine/name string:** muse's process `comm` reports something like
  `muse-bin-1.3.0-R3401.1` — `agent-peer`'s harness auto-detection normalises
  that to a clean `MUSE` engine label and a `muse-<pid>` session name, so
  expect those in `agent-peer list`.
- **Approval:** no Codex-style persistent per-command allowlist at the CLI
  surface. Available flags (all per-launch): `--approval-mode
  untrusted|on-request|never` (default `on-request`), `--disable-approval`,
  `--disable-sandbox`, `--yolo`, `--trust-workspace`, `--sandbox-network
  restricted|enabled|proxy-only`. `trust.json` under `~/.config/muse/`
  persists trust per project, not per command pattern. Launch with
  `--approval-mode never` or `--yolo` to avoid repeated prompts for
  `listen`/`wait` for the rest of the session. There is no saved-settings
  key for this — `--yolo` and friends are launch flags only.
- **Sandbox blocks more than `ps`.** Confirmed with a live syscall probe
  (`/tmp/muse_probe_q2.py`): `kill(other_pid, 0)` returns `EPERM` for every
  process except the caller itself (`kill(self, 0)` is fine); `exec /bin/ps`
  is blocked outright; Unix socket bind is `EPERM` even under `/tmp` (plain
  file writes there are fine — the rejection is at the socket syscall level,
  not the path); writes under `~/.agent-peer/`, `~/.claude/`,
  `~/.config/muse/` are all `EPERM` (reads still work). Net effect without
  escalation: `agent-peer list` shows `ALIVE: no` for every session but your
  own (see the `prune` warning in `SKILL.md`), and harness auto-detection
  falls back to a generic `agent-<pid>` name since it can't exec `ps`.
- **`HERDR_PANE_ID`** (and siblings `HERDR_WORKSPACE_ID`/`HERDR_TAB_ID`) is
  injected into every Herdr-managed pane and stays stable across turns in the
  same pane (confirmed: 3 reads, unchanged). It's the candidate fallback
  identity when `ps` isn't available — check `HERDR_ENV=1` first, the ID is
  empty outside Herdr. Caveat: `herdr`'s own CLI talks over a Unix socket
  (`$HERDR_SOCKET_PATH`), which is *also* `EPERM`'d under the sandbox — so
  under sandbox, the env var is the only part of `herdr` that actually
  works.
- **`agent-peer prune` is genuinely dangerous here without escalation.**
  Since every other session shows `ALIVE: no` under the sandbox, an
  unescalated `prune` would try to delete every registration on the machine,
  not just this session's own stale ones — `agent-peer` refuses this
  specific case by default (all sessions dead at once) and needs an explicit
  `--force` to proceed, precisely because of what this sandbox does.
