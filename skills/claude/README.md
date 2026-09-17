# agent-peer skill for Claude Code

## Install

Claude Code discovers `SKILL.md` files under `.claude/skills/<name>/`
(project) or `~/.claude/skills/<name>/` (personal, global):

```bash
mkdir -p ~/.claude/skills/agent-peer
cp "$(pwd)/SKILL.md" ~/.claude/skills/agent-peer/SKILL.md
```

## Convention notes

- **Discovery:** project `.claude/skills/<name>/SKILL.md`, or personal
  `~/.claude/skills/<name>/SKILL.md`. `name` + `description` frontmatter,
  same as every other harness here.
- **Trigger:** auto-surfaced by description match, or explicit `/agent-peer`
  (Claude Code's `Skill` tool).
- **No `listen`/`wait` needed — this is the one harness that's different by
  design.** Claude Code is the native side of the `/peer` protocol this whole
  project unlocks: every session is already listening and registered the
  moment it starts, and an incoming peer message surfaces directly in
  context on its own. `agent-peer listen`/`wait` exist specifically to give
  non-native harnesses (agy, pi, opencode) the same behavior Claude Code
  already has built in.
- A simpler, project-wide alternative to installing this as a formal skill:
  most users just add the "Commands" section from `SKILL.md` directly into
  their personal `~/.claude/CLAUDE.md`, since it's short enough not to need
  the full skill-discovery machinery.
