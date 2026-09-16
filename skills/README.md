# agent-peer skills

Each harness that can run `agent-peer` has its own skill convention (frontmatter
fields, install location, and how it gets triggered). These folders are the
source of truth — install by symlinking, not copying, so they stay in sync
with this repo instead of silently drifting.

| Harness | Folder | Trigger |
|---|---|---|
| Google Antigravity (agy) | [`agy/`](./agy) | Auto-load by description match, or explicit `/agent-peer` |
| pi | [`pi/`](./pi) | Same as agy — auto-surface + explicit `/agent-peer` |
| opencode | [`opencode/`](./opencode) | No auto-load/slash — explicit `skill({ name: "agent-peer" })` tool call |

Each folder's `README.md` has the exact install command and the convention
notes (location, frontmatter shape, trigger mechanism) for that harness,
verified against its own source/docs during development — not guessed.

If you add support for a new harness, research its own skill convention
first (don't assume it matches an existing one — agy, pi, and opencode each
turned out to work differently) and add a folder here the same way.
