# `agent-peer status` — how the numbers are sourced

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

## Why this exists

In a sequential relay between `agy` and Claude Code (one driving, one on
standby, handing off the baton on rate limits or context pressure), whichever
side is about to make that call needs to know both providers' state — not
just its own. `agent-peer status` is the one place to check either, from
either side, without opening a second terminal.

## `--json` shape

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

Both sides report `remaining_pct` (Anthropic's API reports the inverse,
utilization/used%, so the Claude side flips it before this point) so a script
can compare them directly without knowing each provider's raw convention.

The raw API responses carry a lot more than this (Anthropic's usage endpoint
in particular returns a long tail of `null` fields for unreleased-feature
codenames) — `--json` deliberately keeps only what's actionable for a
continue-or-handoff decision, not a full API dump.

Every `agy.quota.*` entry carries a `"source"`: `"live"` means this call just
fetched it fresh from Google (`fetchAvailableModels`, on demand); `"cached"`
means there's no API for that number at all (weekly quota, context window)
and it's only as fresh as agy's last statusline render (`snapshot_age_s` says
how old). `live_5h_fetch_error` is set instead of silently falling back if the
live call fails, so a consumer isn't fooled into thinking a cached number is
live.

## How each side gets its numbers

**agy** — two sources, because no single one covers everything:
- **5h quota** (`gemini_5h`, `claude_gpt_5h`) — fetched **live, on every
  `agent-peer status` call** (`agy_live.py`), by calling the same
  `v1internal:fetchAvailableModels` endpoint agy itself calls, using agy's own
  OAuth token from the macOS Keychain (service `gemini`, account
  `antigravity`) plus the app-identification headers Google's backend
  requires to answer for non-Gemini models. This is an **undocumented,
  internal** API — not published for third-party use — so it's only ever
  called on demand (once per invocation), never on a timer, to avoid looking
  like automated polling against an endpoint not meant for it. A failed call
  falls back to the cached number instead of retrying (see
  `live_5h_fetch_error`).
- **Weekly quota + context window** — no API exists for these at all (checked:
  `fetchAvailableModels`'s response carries no weekly figure, and context
  usage is purely local state agy itself tracks). These come from
  `~/.gemini/antigravity-cli/statusline.sh` (agy's own
  [statusline hook](https://antigravity.google/docs/cli/statusline/)), which
  already receives this exact data from agy on every render and bridges it to
  `~/.agent-peer/agy_status.json`. Only as fresh as the last time agy actually
  rendered a statusline — `snapshot_age_s` says how stale it is.

**Claude Code** — much simpler. Its own OAuth token (read from the same macOS
Keychain entry Claude Code itself uses — `Claude Code-credentials`, or a
`CLAUDE_CONFIG_DIR`-hashed variant if you run multiple profiles, same
resolution order Claude Code uses) is accepted directly by
`GET https://api.anthropic.com/api/oauth/usage` — no special headers, no
per-product license gate. Fetched live, on every `agent-peer status` call.

One deliberate detail: `CLIENT_ID` in `claude_status.py`
(`9d1c250a-e61b-44d9-88ed-5944d1962f5e`) is Claude Code's own **public** OAuth
client id, used only for the refresh-token exchange — no `client_secret`
involved. Anthropic's CLI OAuth client is a native/public client (PKCE-based),
so this id is meant to be embedded in client code and is already public in
`claude-swap`'s own open-source repo; it's not a credential. Google's
Antigravity OAuth client is the opposite shape (a confidential client needing
both an id *and* a secret), which is the whole reason the agy side needs the
statusline workaround instead of a direct API call.
