# agent-peer skills

Each harness that can run `agent-peer` has its own skill convention (frontmatter
fields, install location, and how it gets triggered). These folders are the
source of truth — install by symlinking, not copying, so they stay in sync
with this repo instead of silently drifting.

| Harness | Folder | Trigger | Receives via |
|---|---|---|---|
| Claude Code | [`claude/`](./claude) | Auto-load by description match, or explicit `/agent-peer` | **Native** — no `listen`/`wait` needed |
| Codex CLI | [`codex/`](./codex) | Auto-surface by description, or explicit `/skills` / `$agent-peer` | **Native** (`codex queue`) when registered with `--codex-thread`; `agent-peer wait` as fallback |
| Google Antigravity (agy) | [`agy/`](./agy) | Auto-load by description match, or explicit `/agent-peer` | `agent-peer wait`, no native push |
| pi | [`pi/`](./pi) | Same as agy — auto-surface + explicit `/agent-peer` | `agent-peer wait`, no native push |
| opencode | [`opencode/`](./opencode) | No auto-load/slash — explicit `skill({ name: "agent-peer" })` tool call | `agent-peer wait`, no native push |

Claude Code and Codex CLI both have their own native inter-session push
mechanism (Claude's `/peer` UDS protocol, Codex's `codex queue`/app-server) —
that's why their skills don't teach a `listen`/`wait` loop the way agy/pi/
opencode's do. Everything still sends the same way: `agent-peer send <peer>`.

Each folder's `README.md` has the exact install command and the convention
notes (location, frontmatter shape, trigger mechanism) for that harness,
verified against its own source/docs during development — not guessed.

If you add support for a new harness, research its own skill convention
first (don't assume it matches an existing one — every harness checked so
far turned out to work differently) and add a folder here the same way.
