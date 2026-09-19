# agent-peer skills

Each harness that can run `agent-peer` has its own skill convention (frontmatter
fields, install location, and how it gets triggered). These folders are the
source of truth — install by symlinking, not copying, so they stay in sync
with this repo instead of silently drifting.

| Harness | Folder | Trigger | Receives via | Needs `wait`? |
|---|---|---|---|---|
| Claude Code | [`claude/`](./claude) | Auto-load by description match, or explicit `/agent-peer` | **Native** (`/peer` UDS) | No — not even `listen` |
| Codex CLI | [`codex/`](./codex) | Auto-surface by description, or explicit `/skills` / `$agent-peer` | **Native** (`codex queue`, auto-registered from `$CODEX_THREAD_ID`) | No — `listen` alone is enough |
| Google Antigravity (agy) | [`agy/`](./agy) | Auto-load by description match, or explicit `/agent-peer` | No native push | Yes — `listen` + `wait` |
| pi | [`pi/`](./pi) | Same as agy — auto-surface + explicit `/agent-peer` | No native push | Yes — `listen` + `wait` |
| opencode | [`opencode/`](./opencode) | No auto-load/slash — explicit `skill({ name: "agent-peer" })` tool call | No native push | Yes — `listen` + `wait` |
| muse (Meta Muse Code) | [`muse/`](./muse) | Auto-surface by description, or explicit `/skills` / `$agent-peer` | No native push found | Yes — `listen` + `wait`, but see the sandbox notes in `muse/README.md` first |

Claude Code and Codex CLI both have their own native inter-session push
(Claude's `/peer` UDS protocol, Codex's `codex queue`) — neither one's skill
teaches a `wait` loop. Codex specifically should avoid `wait` as a standby
mechanism: its runtime caps a blocking call at roughly 60s and resumes it as
a new turn, so a long `wait` quietly burns a turn every ~60s for no work
done. `listen` alone already gets it native delivery for free. Everything
still sends the same way regardless of target: `agent-peer send <peer>`.

Each folder's `README.md` has the exact install command and the convention
notes (location, frontmatter shape, trigger mechanism) for that harness,
verified against its own source/docs during development — not guessed.

If you add support for a new harness, research its own skill convention
first (don't assume it matches an existing one — every harness checked so
far turned out to work differently) and add a folder here the same way.
