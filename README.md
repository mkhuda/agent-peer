# agent-peer

Universal IPC mesh and real-time peer gateway between Claude Code sessions and external agents (Google Antigravity, Hermes, Cursor Agent, and custom scripts).

## Overview

Claude Code features a native cross-session messaging subsystem (`/peer`) powered by Unix Domain Sockets (`/tmp/cc-socks/<pid>.sock`) and per-session authentication tokens (`~/.claude/sessions/<pid>.<token_hash>.key`).

`agent-peer` unlocks this protocol for any agent or shell environment:
- **Instant message delivery (< 10ms)** into running Claude Code sessions without polling.
- **Session discovery**: Inspects all active Claude Code sessions, their states, sockets, and directories.
- **Two-way duplex mesh**: Allows external agents (like Google Antigravity) to register as full peers, enabling Claude Code to reply back directly via `/peer antigravity`.
- **Zero heavy dependencies**: Written in pure, modern Python (3.10+) using the standard library.

## Installation

### Via `uv` (Recommended)

```bash
cd ~/projects/agent-peer
uv tool install --editable . --force
```

This creates the global `agent-peer` command in `~/.local/bin/agent-peer`.

### Direct Python

```bash
python3 -m pip install -e .
```

## Usage

### 1. List active sessions

```bash
agent-peer list
```

Example output:
```text
PID      SESSION NAME             STATUS   ALIVE  SOCKET                       CWD
----------------------------------------------------------------------------------------------------
22748    projects-00              idle     yes    22748.sock                   ~/projects
86622    ottoshare-factory-fe     busy     yes    86622.sock                   .../ottoshare-factory
60526    music-search-engine-tas  busy     yes    60526.sock                   .../ottoshare-factory
3386     ottoshare-factory-d8     busy     yes    3386.sock                    .../ottoshare-factory
```

### 2. Send real-time message to a session

Send by name or PID:
```bash
agent-peer send projects-00 "Halo foreman, ini arahan review dari arsitek!"
```

With custom priority (`now`, `next`, `later`) and sender identity:
```bash
agent-peer send fe "[stop] Harap hentikan edit di file X, lihat spesifikasi di docs." --priority now --sender antigravity
```

### 3. Start a two-way listener (register any harness as a peer)

`agent-peer` isn't Antigravity-specific — any local agent harness (Antigravity,
`pi`, `opencode`, a custom script, ...) can register itself as a peer:
```bash
agent-peer listen --name antigravity
```

When active, Claude Code sessions can simply type in their terminal:
```text
/peer antigravity Laporan: modul caption sudah siap ditinjau.
```

**Auto-detected session names.** `--name` (and `--sender` on `send`, and the
session filter on `wait`) is optional. Leave it out and `agent-peer` walks up
the parent-process chain, skips generic shells/interpreters (`zsh`, `bash`,
`python3`, ...), and uses the first distinctive ancestor process name it finds
— so a harness launched as `pi` registers itself as `pi-<pid>` with zero
configuration, `opencode` as `opencode-<pid>`, and so on. Explicit `--name` /
`--sender` / `$AGENT_PEER_NAME` always take priority when given.

### 4. React to messages without polling (`wait`)

```bash
agent-peer wait --timeout 30
```

This is the reactive trigger a harness runs (typically as its own blocking
tool/shell call) to "wake up" when a peer message arrives — the call blocks,
and returns the moment there's something to read:

- If messages already queued up while the harness was busy doing something
  else, `wait` returns **all of them at once**, immediately, no polling delay.
- Otherwise it blocks and returns as soon as the next message lands (or after
  `--timeout` seconds, exit code 1, if given).
- Each session has its own read cursor (`~/.agent-peer/cursors/<name>.json`) —
  once `wait` returns a message it's marked read automatically, no separate
  "mark as read" step, and it won't be replayed on the next call.
- Calling `wait` twice for the *same* session concurrently is rejected
  immediately (exit code 1) instead of silently racing — useful if your
  harness spawns a new background call without reliably closing the old one.

### 5. Inspect inbox

```bash
agent-peer inbox
agent-peer inbox --limit 10
agent-peer inbox --clear
```

### 6. Check quota / context status (agy + Claude Code)

```bash
agent-peer status                    # both providers
agent-peer status --provider agy     # agy (Antigravity) only
agent-peer status --provider claude  # Claude Code only
agent-peer status --json             # machine-readable, for a peer to parse
```

Example:
```text
── agy (Antigravity) ──────────────────────
agy status  (email: you@example.com · plan: Google AI Pro · model: Gemini 3.8 Flash (Medium))
  snapshot age: 1m ago

  context: 11.3% used (118.0k in / 17.1k out / 1.0M window)

  quota:
    gemini   5h     76% remaining  (resets in 1h58m)
    gemini   week   40% remaining  (resets in 121h21m)
    claude/gpt 5h  100% remaining  (resets in 4h59m)
    claude/gpt wk  100% remaining  (resets in 167h59m)

── claude (Claude Code) ────────────────────
claude status  (email: you@example.com · plan: max)

  quota:
    session   5h    46.0% remaining  (resets in 51m)
    weekly    all   73.0% remaining  (resets in 139h51m)
    weekly    fable 83.0% remaining  (resets in 139h51m)
```

`--json` returns the same numbers in a compact, provider-agnostic shape —
both sides report `remaining_pct` (Anthropic's API reports the inverse,
utilization/used%, so the Claude side flips it before this point) so a
script can compare them directly without knowing each provider's raw
convention:

```json
{
  "agy": {
    "email": "...", "plan": "Google AI Pro", "model": "...",
    "snapshot_age_s": 12.3,
    "live_5h_fetch_error": null,
    "context": { "used_pct": 11.3, "input_tokens": 118000, "output_tokens": 17100, "window_size": 1048576 },
    "quota": {
      "gemini_5h": { "remaining_pct": 52.9, "resets_in_s": 4097, "source": "live" },
      "gemini_weekly": { "remaining_pct": 39.7, "resets_in_s": 436366, "source": "cached" },
      "claude_gpt_5h": { "remaining_pct": 100, "resets_in_s": 17998, "source": "live" },
      "claude_gpt_weekly": { "remaining_pct": 100, "resets_in_s": 604798, "source": "cached" }
    }
  },
  "claude": {
    "email": "...", "plan": "max",
    "quota": {
      "session_5h": { "remaining_pct": 46.0, "resets_in_s": 3106 },
      "weekly_all": { "remaining_pct": 73.0, "resets_in_s": 503506 },
      "weekly_fable": { "remaining_pct": 83.0, "resets_in_s": 503506 }
    }
  }
}
```

The raw API responses carry a lot more than this (Anthropic's usage endpoint
in particular returns a long tail of `null` fields for unreleased-feature
codenames) — `--json` deliberately keeps only what's actionable for a
continue-or-handoff decision, not a full API dump.

Every `agy.quota.*` entry carries a `"source"`: `"live"` means this call just
fetched it fresh from Google (`fetchAvailableModels`, on demand — see
below); `"cached"` means there's no API for that number at all (weekly
quota, context window) and it's only as fresh as agy's last statusline
render (`snapshot_age_s` says how old). `live_5h_fetch_error` is set instead
of silently falling back if the live call fails, so a consumer isn't fooled
into thinking a cached number is live.

**Why this exists**: in a sequential relay between `agy` and Claude Code (one
driving, one on standby, handing off the baton on rate limits or context
pressure), whichever side is about to make that call needs to know both
providers' state — not just its own. `agent-peer status` is the one place to
check either, from either side, without opening a second terminal.

**How each side gets its numbers — two different mechanisms:**

- **agy**: two sources, because no single one covers everything.
  - **5h quota (`gemini_5h`, `claude_gpt_5h`)** — fetched **live, on every
    `agent-peer status` call** (`agy_live.py`), by calling the same
    `v1internal:fetchAvailableModels` endpoint `agy` itself calls, using
    agy's own OAuth token from the macOS Keychain (service `gemini`, account
    `antigravity`) plus the app-identification headers Google's backend
    requires to answer for non-Gemini models. This is an **undocumented,
    internal** API — not published for third-party use — so it's only ever
    called on demand (once per invocation), never on a timer, to avoid
    looking like automated polling against an endpoint not meant for it. A
    failed call falls back to the cached number instead of retrying (see
    `live_5h_fetch_error`).
  - **Weekly quota + context window** — no API exists for these at all
    (checked: `fetchAvailableModels`'s response carries no weekly figure,
    and context usage is purely local state agy itself tracks). These come
    from `~/.gemini/antigravity-cli/statusline.sh` (agy's own
    [statusline hook](https://antigravity.google/docs/cli/statusline/)),
    which already receives this exact data from agy on every render and
    bridges it to `~/.agent-peer/agy_status.json`. Only as fresh as the last
    time agy actually rendered a statusline — `snapshot_age_s` says how
    stale it is.

- **Claude Code**: much simpler. Its own OAuth token (read from the same
  macOS Keychain entry Claude Code itself uses — `Claude Code-credentials`,
  or a `CLAUDE_CONFIG_DIR`-hashed variant if you run multiple profiles, same
  resolution order Claude Code uses) is accepted directly by
  `GET https://api.anthropic.com/api/oauth/usage` — no special headers, no
  per-product license gate. Fetched live, on every `agent-peer status` call.

  One deliberate detail: `CLIENT_ID` in `claude_status.py`
  (`9d1c250a-e61b-44d9-88ed-5944d1962f5e`) is Claude Code's own **public**
  OAuth client id, used only for the refresh-token exchange — no
  `client_secret` involved. Anthropic's CLI OAuth client is a native/public
  client (PKCE-based), so this id is meant to be embedded in client code and
  is already public in `claude-swap`'s own open-source repo; it's not a
  credential. Google's Antigravity OAuth client is the opposite shape (a
  confidential client needing both an id *and* a secret), which is the whole
  reason the agy side needed the statusline workaround instead of a direct
  API call — see [`~/projects/agy-explore`](../agy-explore) for the full
  investigation.

## Architecture & Protocol

Claude Code enforces process ownership verification before accepting IPC connections:
1. **PID validation**: Checks if the target process is running.
2. **Process start time verification**: Matches `LC_ALL=C TZ=UTC ps -o lstart= -p <pid>`.
3. **Auth token handshake**: First line must be `{"type": "auth", "token": "<peerToken>"}\n`.
4. **Message frame**: Followed by `{"type": "user", "priority": "now", "from": "...", "message": {"content": "..."}}\n`.

`agent-peer` automatically handles this entire handshake, socket binding, token generation, and graceful session lifecycle cleanup.

## License

MIT License.
