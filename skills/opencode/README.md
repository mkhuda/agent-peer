# agent-peer skill for opencode

## Install

Copy or symlink `SKILL.md` into opencode's global skills directory:

```bash
mkdir -p ~/.config/opencode/skills/agent-peer
ln -sf "$(pwd)/SKILL.md" ~/.config/opencode/skills/agent-peer/SKILL.md
```

## Convention notes

(Confirmed against opencode's own docs, `opencode.ai/docs/skills/`.)

- **Discovery order** (first match wins): project `.opencode/skills/<name>/SKILL.md`,
  global `~/.config/opencode/skills/<name>/SKILL.md`, project/global Claude-compatible
  `.claude/skills/` / `~/.claude/skills/`, project/global agent-compatible
  `.agents/skills/` / `~/.agents/skills/`. This repo installs to the global opencode
  path — don't also drop a copy under `~/.claude/skills/`, that would affect real
  Claude Code sessions on this machine, not just opencode.
- **Frontmatter:** `name` and `description` are required; `license`, `compatibility`,
  and `metadata` are optional. `name` must be lowercase, hyphen-separated
  (`^[a-z0-9]+(-[a-z0-9]+)*$`) and match the containing folder's name.
- **Trigger:** opencode has **no auto-load and no slash command** for skills. The
  model sees every installed skill's name + description in context and invokes one
  explicitly via a tool call, `skill({ name: "agent-peer" })`. This is a real
  behavioral difference from Antigravity/pi's `/agent-peer` slash trigger.
- opencode's bash tool defaults to **synchronous with a short timeout** (observed as
  low as 5 seconds in one real session, not the ~2 minutes generic docs suggested —
  don't assume either number). The skill content deliberately doesn't hardcode which
  behavior your setup has; it asks the agent to check first.
