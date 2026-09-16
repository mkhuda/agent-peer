# `agent-peer` review — Claude Code perspective (this session's implementer)

**From:** Claude Code session `agent-peer-e4` (did most of the fixes in this
collaboration session)
**Context:** part of a 4-way collaboration (agy, pi, opencode, Claude) to find
remaining gaps and mature `agent-peer` toward publish-readiness.

Focus of this review: consolidating the backlog that's ALREADY known but not
yet worked on (so it doesn't get lost among this session's many changes),
plus a few project-maturity gaps that haven't been touched at all.

---

## 1. Security/reliability backlog already found, not yet worked on

All of these come from `docs/agent-peer-weaknesses-report.md` (written by an
earlier `antigravity-test` session) — checked and still valid, none of them
have been worked on:

1. **File permissions** — `inbox.jsonl`, `inboxes/*.jsonl`, `cursors/*.json`,
   `locks/*.lock` are all created with the default umask (not `chmod 0600`
   like the socket & key file). Another user on the same machine can read
   the entire chat history in plain text. Cheap fix: `os.chmod(path, 0o600)`
   whenever these files are first created/written.
2. **Race condition from non-atomic writes** — `append_inbox` (`inbox.py`)
   uses plain `open(...,"a")` with no `fcntl.flock`. This is genuinely
   cross-PROCESS, not just cross-thread: the global `INBOX_FILE` is shared
   by EVERY listener running on the machine (agy, pi, opencode, etc. all at
   once). If 2 listeners write a large line at the same moment, interleaving
   can corrupt that line's JSON parsing (silently dropped in `read_inbox`,
   not a crash — more dangerous because it's invisible).
3. **`agent-peer inbox --clear` doesn't reset the cursor** — a low-severity
   edge case (needs the clock to go backward to actually break), but still
   counter-intuitive: a user expecting "clear = reset everything" will be
   confused that unread behavior isn't also reset.
4. **Symlink squatting on `/tmp/cc-socks/`** — low severity (the /tmp sticky
   bit already protects most of it), but still worth a stricter `os.chmod`
   on the socket directory if you want real protection from other local
   users.
5. **`osascript`/`terminal-notifier` spam has no throttling** during a fast
   message burst — low risk for personal use, but if `agent-peer` gets
   published and used by someone with higher traffic, this could become a
   real problem (many notification processes spawned with no throttle).

## 2. `docs/stale-listener-detection.md` is still a plan, no code

There's already a full analysis (why "detached" detection is invalid, why
it should use duplicate-name + recency + an explicit `stop` command) but
**not a single line of code for it exists yet**. `antigravity`/
`antigravity-2` are still stuck in `agent-peer list` right now (since the
start of this session) as living proof this hasn't been addressed.

## 3. Auto-name collision risk on a multi-child-per-parent architecture

*(I see agy's draft summary also touches this from its own angle — I'm
noting my own version because the implications matter.)*

`detect_harness_identity()` uses the PID of the **first non-generic-shell
ancestor process** as a stable identity. This is valid for the "one
window/session = one clear harness process" case. But if a single parent
process (e.g. one Antigravity app, PID 33402) runs **many parallel
sub-sessions/tabs** that all share that exact same parent process, they'll
all resolve to an **identical** auto-generated name (`agy-33402`) — inbox,
cursor, AND lock would end up unknowingly shared between sub-sessions that
are actually independent. This hasn't actually been tested this session
(all our tests happened to have 1 harness = 1 unique parent process), so
it's still theoretical, but the architecture is real and worth verifying
before publishing.

## 4. Project maturity for publishing (not touched at all this session)

1. **No automated test suite in the repo.** Every verification this session
   (cursor, lock, auth-gate, etc.) was done via ad-hoc scripts in the
   scratchpad session — none of it became a permanent `tests/` in the repo.
   If published, other contributors have no safety net against regressions.
2. **Version is still `0.1.0`** (`pyproject.toml`) despite several big
   features added this session (unread cursor, lock, auto-name,
   engine-detection, cwd-fix, auth-fix). No `CHANGELOG.md` at all.
3. **Skill templates aren't in the repo** — this was a question the user
   asked earlier in this collaboration session that wasn't fully answered
   at the time: `SKILL.md` for agy/pi/opencode only lived in each LOCAL
   user's own `~/.gemini/...`/`~/.pi/...`/`~/.config/opencode/...`, with NO
   copy in the `agent-peer` repo itself. If published as-is, anyone cloning
   this repo gets no skill files at all — they'd have to reconstruct them
   from scratch. Recommendation: add a `skills/<harness>/SKILL.md` folder in
   the repo as the source of truth, then document how to symlink/copy it
   into each tool's own location in the README (ideally via a small install
   script, to stay in sync the same way `uv tool install --editable` does).
4. **No `agent-peer stop`/`prune` command** — discussed in
   `docs/stale-listener-detection.md`, still just a plan.

## 5. What's already PROVEN solid (so this isn't just a list of gaps)

- Cursor/backlog-merge, lock, auto-name, engine-detection, cwd-fix — all
  validated end-to-end across 3 different harnesses (agy, pi, opencode)
  plus a real 3-way relay between them. This isn't a claim — there's file
  evidence (cursor/inbox/lock timestamps) cross-checked every time.
- Auth-bypass (token was never enforced) — found and fixed, tested for
  correct reject/accept behavior.

## Priority recommendation if this gets acted on

Realistic order (cheap→expensive, highest impact first):
1. `chmod 0600` on inbox/cursor/lock files (cheap, clear security impact).
2. A basic test suite (`tests/`) before publishing — at minimum cover
   cursor logic + lock, since that's the most prone to silent regression.
3. Skill templates into the repo + an install script.
4. The rest (write race condition, `--clear` resetting the cursor,
   stale-listener command, auto-name collision) — document as known
   limitations in the README if there's no time to fix them before
   publishing, to stay honest with prospective users.
