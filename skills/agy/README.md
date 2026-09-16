# agent-peer skill for Google Antigravity (agy)

## Install

Copy or symlink `SKILL.md` into Antigravity's skills directory:

```bash
mkdir -p ~/.gemini/antigravity/skills/agent-peer
ln -sf "$(pwd)/SKILL.md" ~/.gemini/antigravity/skills/agent-peer/SKILL.md
```

(Symlinking keeps it in sync with this repo instead of drifting from a one-time copy.)

## Convention notes

- **Location:** `~/.gemini/antigravity/skills/<name>/SKILL.md` — one folder per skill,
  folder name matches the skill.
- **Frontmatter:** `name` + `description` only. Freeform markdown body, no mandated
  section structure.
- **Trigger:** two mechanisms, both confirmed working live:
  1. **Auto-load by description match** — Antigravity's own global instructions
     (`~/.gemini/GEMINI.md`, the "SuperAntigravity Skills" framework) mandate checking
     installed skills' descriptions before acting on anything, and loading the full
     content of a matching one.
  2. **Explicit slash command** — `/agent-peer` (matching the skill's `name:` field)
     works directly as a trigger in the Antigravity CLI once the skill is installed.
- Antigravity also needs a short addition to its own global config to know
  `agent-peer` exists at all before the skill can even be reached — see the
  `## Cross-agent messaging (agent-peer)` section this project's session added to
  `~/.gemini/GEMINI.md` (not shipped here since it's Antigravity's own global file,
  not something this repo owns).
