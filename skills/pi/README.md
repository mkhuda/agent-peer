# agent-peer skill for pi

## Install

Copy or symlink `SKILL.md` into pi's **native** skills directory — not the
`pi-hermes-memory` extension's own managed store (see convention notes below):

```bash
mkdir -p ~/.pi/agent/skills/agent-peer
ln -sf "$(pwd)/SKILL.md" ~/.pi/agent/skills/agent-peer/SKILL.md
```

## Convention notes

- **Location:** `~/.pi/agent/skills/<name>/SKILL.md` is pi's own canonical global
  skills root, read directly by pi's core engine — confirmed from the
  `pi-hermes-memory` extension's own source (`skill-store.ts`), which deliberately
  writes its LLM-managed procedural-memory skills to a **different** directory
  (`~/.pi/agent/pi-hermes-memory/skills/`) specifically so it never shadows or
  conflicts with pi's own root. Don't confuse the two — this skill belongs in the
  native root, alongside pi's bundled skills (e.g. `web-search`).
- **Frontmatter:** `name` + `description` only, matching the simple format pi's own
  bundled skills use. The more elaborate "When to Use / Procedure / Pitfalls /
  Verification" structure is only enforced by the `skill_manage` tool for skills an
  agent creates from its own experience — it's not required for a file placed
  directly in the native skills root.
- **Trigger:** same as Antigravity — `/agent-peer` works as an explicit slash
  command (pi's internal command registry exposes each skill as `skill:<name>`,
  stripped to `/<name>` for display), confirmed working live. It also gets surfaced
  to the model as an available skill by name + description for it to decide to use.
- pi's bash tool has **no background-task feature** (confirmed by pi auditing its
  own tool source) — this is the reason the skill content here handles `listen`
  and `wait` differently from Antigravity's version.
