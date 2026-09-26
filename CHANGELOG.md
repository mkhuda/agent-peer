# Changelog

## 0.9.2

- **Fixed:** a session that started `listen --name <foo>` as a subprocess of its harness
  (agy, muse, pi, opencode) could still show up under an auto-generated
  `<harness>-<pid>` name in `agent-peer thread`/`join`/`send`, instead of the name it
  registered - `listen`'s own subprocess pid isn't the harness's own pid, so the two
  never matched. Every command started from the same harness now reuses the name that
  `listen` registered.

## 0.9.1

- **Tab-completes `@mentions` in `agent-peer join`**: type `@` plus a few letters and press
  Tab to complete it against a live candidate pool - this thread's own participants
  (regardless of project) union any other alive session in the same workspace, same scoping
  `setup`'s workspace-scoped summons already uses. Repeated Tab (with no other typing in
  between) cycles through multiple matches instead of only ever completing the first one.

## 0.9.0

- **Redesigned shared-thread view** (`agent-peer join`/`thread`/`logs --thread`): each
  sender now gets a filled, per-harness-colored badge (`CLAUDE`/`CODEX`/`AGY`/`MUSE`/`PI`/
  `OPENCODE`, plus a distinct `YOU` badge for your own posts) instead of a plain divider
  bar. Consecutive posts from the same sender group together - the badge/name header only
  repeats when the sender actually changes, chat-app style, instead of once per message.
  Harness-type detection also now recognizes every known harness by name pattern (not
  just Antigravity), so a session no longer in the live registry still gets the right
  color instead of silently defaulting to Claude.
- Fixed: the interactive `join` input line ignored Left/Right arrow keys entirely (typing
  after pressing Left always landed at the end of the line, not where the cursor visually
  was). Left/Right now move the cursor character-by-character; Option/Alt+Left/Right or
  Ctrl+Left/Right jump by word, matching standard terminal editing conventions.

- Changed: a code fence opened without a matching close now extends to the end of the
  message, matching how Markdown itself renders it - any `@mention` or `[stop]` after an
  unclosed fence is treated as still inside it and does not knock. Previously the opener
  was treated as literal text, which risked the opposite failure this whole fix chain
  addressed: an accidental summon from a name that happened to follow a stray, unintended
  fence marker.

## 0.8.8

- Fixed: the `[stop]` urgency keyword bypassed the code-span stripping added for `@mention`s
  - a message quoting the `[stop]` prefix convention as a literal example (in backticks or a
  fenced block) still triggered a real stop-knock. It now goes through the same stripping.

## 0.8.7

- Fixed: fenced code block stripping (0.8.4) closed on any occurrence of the fence
  delimiter, including one appearing mid-line inside the block's own content (e.g. a
  code example that itself mentions a fence). A closing fence now has to be alone on its
  own line, per the same rule Markdown itself uses, so content after a mid-line delimiter
  stays correctly inside the block.

## 0.8.6

- Fixed: inline code-span stripping (0.8.4/0.8.5) used a regex that could mispair backtick
  runs of different lengths inside a single span, leaving a mention after a stray backtick
  run unstripped. Inline code spans are now matched by finding the next run of exactly the
  same backtick count, per the same rule Markdown itself uses, instead of a regex
  approximation.

## 0.8.5

- Fixed: the code-span detection added in 0.8.4 only recognized triple-backtick fences and
  single-line inline code. Tilde fences (`~~~`), multi-line inline code spans, and longer
  backtick-run delimiters are now excluded from mention detection too.

## 0.8.4

- Fixed: thread `@mention` summons could notify a session in an unrelated workspace when mentioned in plain text. Summons are now scoped to the sender's own workspace by default; mentions quoted inside inline or fenced code spans are also excluded from summon detection.

## 0.8.3

- Fixed two follow-up gaps in 0.8.2's agy quota-matching fix, found in review: the token
  matcher used substring containment, so hint tokens like "3"/"8" falsely matched an
  unrelated "13.8" model; matching is now done on whole tokens instead. Also, a hint that
  matched no model in the response at all no longer silently falls back to an unrelated
  model's number - it's reported as unavailable, same as when the active model matches but
  carries no quota data of its own.

## 0.8.2

- Fixed: `agent-peer status`'s live 5h quota reading for `agy` (Antigravity) could report a
  misleadingly high number when Google's `fetchAvailableModels` endpoint didn't track the
  currently active model's quota at all - an unrelated, untouched sibling model still sitting
  at 100% was being picked up by a blind "most-constrained-of-everything" heuristic instead.
  The live fetch now matches the model actually in use (from the cached statusline snapshot)
  and, when that exact model has no quota data of its own, reports it as unavailable rather
  than substituting a different model's number.

## 0.8.1

- **Global mesh policy injection in `agent-peer setup`**: `--rules` injects a compact,
  idempotent managed marker block into each harness's global instruction file
  (`~/.codex/AGENTS.md`, `~/.claude/CLAUDE.md`, `~/.gemini/GEMINI.md`, `~/.agents/AGENTS.md`,
  `~/.pi/agent/AGENTS.md`, `~/.config/opencode/AGENTS.md`), so a fresh session in any
  workspace knows core mesh invariants from turn 1 instead of waiting on a lazily-loaded
  skill. `--no-rules` removes just the managed block, byte-exact, leaving the rest of the
  file untouched; re-running `--rules` updates the block in place without duplicating it.
  `--rules`/`--no-rules` are mutually exclusive and opt-in (unchecked by default in the
  interactive picker) - plain `agent-peer setup` still only installs skills, unchanged.
- Updated `skills/agy/SKILL.md`, `skills/codex/SKILL.md`, and `skills/claude/SKILL.md`
  with explicit per-harness standby invariants and a turn-end checklist, grounded in
  live-verified runtime behavior rather than assumed worst-case claims.
- Fixed: cancelling the interactive `agent-peer setup` picker (`q`, `Esc`, or Ctrl+C)
  used to fall through to "all set - every selected harness's skill is already
  installed", which read like the picker had silently confirmed and installed
  something. It now prints "setup cancelled - no changes made" and exits without
  touching install/remove/rules at all.

## 0.8.0

- **`follow-all` opt-in for shared threads**: `agent-peer thread <id> --timeout N --follow`
  marks a gated participant to receive every other participant's post to that thread, not
  just explicit `@mention`s - a standing preference that persists across later peeks until
  `--leave`. Costs one native push per message while active (disclosed as a real,
  per-message cost, not the default recommendation).
- Fixed: a native push to a target whose delivery takes longer than ~200ms (e.g. a
  `codex queue` delivery, which spawns a separate process and can take over a second even
  when healthy) could be silently dropped - the sending process's own background delivery
  was cut short before it finished. The delivery window is now generous enough for a
  genuinely slow-but-successful target while still bounded overall.

## 0.7.1

- A native push delivered by a shared thread now carries its own reply instructions -
  the exact `agent-peer send --thread <id> "..."` command a recipient should answer
  with, not just the thread id. Previously that convention only lived in the skill
  docs, so a session that skipped them had no in-the-moment cue and could reply
  straight back to whoever posted instead of into the room.

## 0.7.0

- **Smarter invite picker in `join`**: the picker now opens automatically whenever a
  room has no other active member (not just on a brand-new thread), and only offers
  sessions that aren't already active - a soft-left one (e.g. one just peeking) stays
  a legitimate re-invite target instead of disappearing from the list. `/invite` inside
  the live chat re-opens the picker on demand without quitting.
- **Harness-aware invite text**: an invite DM no longer tells every recipient to run the
  same indefinite wait command - a harness without a persistent poll loop gets pointed at
  its own bounded-peek idiom instead, so it can't get stranded unreachable.
- Fixed: an invite could silently fall back to generic wording for a real native Claude
  Code session, because the session's own reported engine name didn't match the
  case-sensitive check.
- Fixed: a transient filesystem error (e.g. a full disk) while touching thread presence
  could permanently kill the live view's background updates for the rest of that session
  instead of just skipping one refresh.
- `agent-peer thread --help` / `agent-peer join --help` now state plainly that an
  indefinite wait must be looped to stay useful, and point at the per-harness skill for
  how - previously only the skill docs said this, so any usage that skipped them missed
  it entirely.

## 0.6.0

- **Shared threads**: multi-party rooms on top of 1-to-1 messaging. Post with
  `send --thread <id>`, wait with `thread <id>`, step out with
  `thread <id> --leave`, or sit in the live view with `join <id>`.
- **Zero-push room doctrine**: an active member reads the room stream via its
  own poll and is never socket-pushed, not even on mention. The socket is
  strictly a doorbell: it rings only for members who stepped out (or never
  joined) on an explicit `@name`/`@all`/`[stop]`, without enrolling them.
- **Join/leave notices**: arrivals and departures appear as plain ambient
  lines in the room stream itself, so no separate command is needed to see
  who is around. Exiting the live view marks a real departure.
- **Hardened terminal input**: arrow keys no longer wipe the composed line,
  bracketed paste lands as one message, and Ctrl+D exits cleanly.

## 0.5.2

- **Fixed: a session could vanish from `list`/`send` even with no concurrent write happening.**
  A native Claude Code session's own registry file (`~/.claude/sessions/<pid>.json`) was found with
  a literal extra trailing `}` - confirmed persistent (5/5 consistent reads, no writer active during
  the window), not a race, and not written by agent-peer's own code. `get_active_sessions()` now
  falls back to `JSONDecoder().raw_decode()` to recover the leading valid object when `json.loads()`
  rejects trailing garbage, instead of dropping the session entirely.

## 0.5.1

- **Fixed: a genuine race could make a session invisible to `send`/`list` while it was actively
  receiving messages.** Every session status update (`listener.py`'s new-message/idle transitions,
  `cli.py`'s post-`wait` idle reset) wrote its JSON file with `open(path, "w")` + `json.dump()` -
  which truncates the file before writing. A concurrent reader (another session's `list` or `send`)
  landing in that window saw a corrupt/empty file, which `get_active_sessions()` silently dropped
  from its results - "Active sessions: None" even though the target was genuinely alive and busy.
  Reproduced live: 770 corrupt reads out of 300 writes under concurrent access; 0 after the fix.
  Every session JSON write now goes through `agent_peer.protocol.atomic_write_json()` (temp file +
  `os.replace()`, atomic on both POSIX and Windows).
- Confirmed live and documented in `skills/codex/SKILL.md`: message content with backticks/`$()`/
  `${...}` is safe over the wire (`agent-peer`'s own JSON framing never reinterprets it) - but a
  caller invoking `agent-peer send` through a double-quoted shell string can still have the *shell
  itself* expand those before agent-peer ever sees the argument. Single-quote the message instead.

## 0.5.0

Native Windows support - `agent-peer` now runs directly on Windows, no WSL needed. Every
OS-specific call is isolated behind one new module, `agent_peer/compat.py`, and every Windows path
was verified live against a real Windows 11 machine over SSH, the same way every other harness in
this project has been verified rather than assumed.

- **Transport**: Windows has no `socket.AF_UNIX` (confirmed absent on a real install, not just
  "supported with caveats" as commonly assumed) - the mesh now speaks Named Pipes there via
  `multiprocessing.connection`, using its private raw-byte I/O (`_send_bytes`/`_recv_bytes`, which
  call `WriteFile`/`ReadFile` directly - verified against the actual CPython source, not just
  behavior, after a reviewer initially suspected these still added framing) so the wire format
  stays identical to POSIX's raw `AF_UNIX` bytes.
- **Locking**: `fcntl.flock` doesn't exist on Windows at all - replaced with `O_CREAT|O_EXCL`
  (atomic on both platforms) plus pid-tracking for crash-safety, since a plain lock file isn't
  auto-released on crash the way `flock` was. Verified live: a killed holder's lock is correctly
  reclaimed by the next caller.
- **Process introspection**: `ps`-based liveness/start-time/parent-walk replaced with `ctypes`
  calls against `kernel32` (`OpenProcess`, `GetProcessTimes`, `CreateToolhelp32Snapshot`) - no
  `psutil` dependency. Harness auto-detection now also skips the Windows shell family
  (`cmd`/`powershell`/`pwsh`/`conhost`) and strips a `.exe` suffix before matching.
- **Permissions**: owner-only file/directory hardening now uses `icacls` on Windows instead of
  `chmod`. Directories are only secured once, on first creation, not on every call - found via a
  live latency measurement (5 message appends: 0.325s -> 0.011s, ~30x) after review flagged the
  per-message overhead.
- **`agent-peer setup`'s picker** works on Windows via stdlib `msvcrt` - no `windows-curses`
  dependency added.
- **`install.ps1`**: a PowerShell 5.1-compatible one-door installer, mirroring `install.sh`'s
  smart-detection chain (Python 3.10+ probe, `uv`/`pipx`/`pip` fallback chain, PATH sanity check).
- `get_harness_cwd()` (cross-process cwd resolution) deliberately does **not** attempt a Windows
  equivalent - the only route (reading another process's PEB via undocumented
  `NtQueryInformationProcess`) was judged too fragile to be worth it; it already degrades to `None`
  gracefully there.

## 0.4.4

- New `install.sh` + `agent-peer setup`: one-door installation. `install.sh` is a thin POSIX `sh`
  bootstrap - detects an existing install, checks platform/Python 3.10+/an installer (`uv` -> `pipx`
  -> `pip --user`, first hit wins) with a clear message at whichever link is missing, then hands off
  to `agent-peer setup`. `setup` detects which of the six supported harnesses (agy, Codex, muse, pi/
  oh-my-pi, opencode, Claude Code) are actually present and opens a stdlib `curses` checkbox picker
  (no new dependency) to install or remove each one's `SKILL.md` at its known path; `--all`/
  `--harness`/`--remove`/`--list` cover non-interactive/scripted use. Skills ship inside the wheel
  itself (`importlib.resources`, verified against a real built wheel in an isolated venv+`$HOME`) so
  a `pip`/`uv tool` install never needs the source repo on disk.
- New background update notice: after any command, a cache-first check (24h TTL, 2.5s timeout,
  silent on any failure) prints one stderr-only line when a newer PyPI release exists - never stdout
  (keeps `--json`/piped output clean), never in a non-interactive context, never auto-upgrades.

## 0.4.3

- New `agent-peer status` Codex provider: quota/rate-limit percentages and reset times for
  ChatGPT-plan Codex CLI sessions, sourced live from `codex app-server` over stdio JSON-RPC (no
  extra credentials beyond an already-logged-in Codex session). Covers every bucket the CLI itself
  shows, including the "Luna Reserve" weekly pool.
- New `agent-peer status --live`: a refreshing ANSI dashboard across agy/Claude/Codex quota, colored
  bars, no new dependency. `agent-peer status` and `--json` are unchanged - `--live` is a strict
  addition, refuses cleanly outside an interactive terminal or combined with `--json`.

## 0.4.2

- New `agent-peer send <peer> "msg" --await-reply [seconds]`: after sending, blocks the same call
  for the target's reply instead of needing a separate `wait`. Closes a real race found in live
  muse usage - a reply arriving just 9 seconds after a send was still missed because `wait` wasn't
  re-armed yet. Bare flag waits indefinitely; a timeout exits 1. Never touches the read cursor, so
  a later `wait` still delivers the same reply.
- **Fixed: `agent-peer listen` could register a duplicate session for the same harness session** -
  confirmed live (Codex thread registering as both `codex-8763` and `codex-8763-2`). `listen` now
  recognizes a second call from the same harness session (matched by Codex thread id or
  `HERDR_PANE_ID`) and refuses, naming the already-running session, instead of minting a new one.
  `--force` keeps the old behavior for deliberate cases.

## 0.4.1

Adds muse (Meta Muse Code) as a supported harness, plus safety fixes found through live testing
against it.

- Ships `skills/muse/` — muse has no native inbound push, so `wait` is mandatory there like
  agy/pi/opencode, with sandbox-specific notes (approval flags, `kill(pid, 0)` returning `EPERM`
  for other processes, `HERDR_PANE_ID` as an identity fallback).
- **Fixed: `agent-peer wait` blocked forever if `listen` was never run for that session first** -
  confirmed live, a session called `wait` without registering, saw the "not reachable" warning,
  and hung anyway waiting for a message nobody could ever send. `wait` now refuses immediately
  (exit 1) instead. Every wait-loop skill (agy/pi/opencode/muse) now states the `listen`-before-
  `wait` ordering as an explicit rule instead of leaving it implied.
- **Fixed: `agent-peer prune` could wipe every session's registration at once** if the caller's own
  sandbox made `kill(pid, 0)` return `EPERM` for every other process (confirmed live under muse) -
  every session looked dead, and prune would have deleted all of them. Now refuses when 100% of
  sessions appear dead simultaneously; `--force` overrides.
- Harness auto-detection strips a versioned `<name>-bin-<version>` process name (muse's own binary
  is literally `muse-bin-1.3.0-R3401.1`) down to the plain name, so session names and the ENGINE
  column stay short and don't change on every version bump.

## 0.4.0

A round of real bugs found through live multi-harness use (agy in particular), plus two new
housekeeping commands.

- **Fixed: a sender's readable name was silently replaced with a raw socket path**
  (`uds:/tmp/cc-socks/<pid>.sock`) whenever the sender itself was a registered session - the
  common case, so most real sends hit it. Nothing downstream resolved it back to a name; the
  recipient just saw a bare PID number. Always uses the real name now.
- **Fixed: native Claude Code recipients never saw sender/cwd info at all.** Claude's own binary
  renders a peer message from its content alone - it never surfaces the `from`/`from_cwd` fields
  from the frame. A `[from <name> · <cwd>]` header is now prepended to the message text itself for
  native Claude and `codex queue` targets, the only two paths where this info would otherwise be
  lost; `agent-peer wait`'s own printed output (the primary way agy/pi/opencode/codex actually see
  a new message) also picked up the same cwd info it was missing.
- **Fixed: `agent-peer listen` registered wherever the tool call happened to run, not the
  harness's actual home directory.** If the model `cd`'d elsewhere (or a tool call ran with an
  explicit per-call working directory) before calling `listen`, the registration drifted with it -
  even though the interactive session itself never moved. Now prefers the harness process's own
  OS-tracked cwd (`lsof -d cwd` on macOS, `/proc/<pid>/cwd` on Linux), which doesn't drift just
  because one tool call runs elsewhere. An explicit `--cwd` still wins if given.
- `agent-peer list` now shows a live `busy`/`idle` status for agy sessions, sourced from agy's own
  statusline cache - previously it only ever showed `idle`/`new-msg` from agent-peer's own
  tracking, since agent-peer has no visibility into whether the model is actually "thinking."
- New `agent-peer prune` removes registrations for sessions whose process is confirmed dead (e.g.
  killed with `Ctrl+C`, which can `SIGKILL` a background child and skip its own cleanup) - never
  touches a session that's still alive.
- New `agent-peer list --cwd <substring>` narrows the list to one project on a machine running
  many unrelated sessions.
- Every message record now carries `from_cwd` - purely informational context shown in
  `inbox`/`watch`/`logs`/`wait`, never used for addressing or session identity.
- Fixed a real anti-pattern in agy's skill: it told the model not to pass `--name antigravity` by
  literally spelling out that exact forbidden string, which likely caused it to do exactly that -
  traced live to a stray duplicate registration. Rephrased without repeating the banned string.

## 0.3.0

Adds Codex CLI as a fully-supported harness, and gives it (together with
Claude Code) native inbound delivery instead of the generic file-mailbox
every other harness uses.

- `agent-peer listen --codex-thread <uuid>` registers a Codex session with
  its own native thread UUID. Once registered that way, `agent-peer send`
  delivers to it via `codex queue` directly - no `listen`/`wait` loop needed,
  confirmed live against a real Codex CLI session.
- Fixed a logging gap: any delivery that bypasses agent-peer's own socket
  listener (the new Codex-native path, and - previously unnoticed - every
  message sent to a real native Claude Code session) never showed up in
  `agent-peer watch`/`logs`/`inbox`. Both paths now log on the sender's side.
- Ships `skills/codex/` (Codex CLI) and `skills/claude/` (Claude Code) -
  the two harnesses with native delivery, so neither one's skill teaches a
  `wait` loop it doesn't need.
- README and the architecture diagram no longer frame agent-peer as
  "unlocking Claude's protocol for everyone else" - it's a mesh that uses
  each harness's own native transport when one exists (Claude, Codex) and a
  shared socket + `wait` fallback for the rest (agy, pi, opencode).

## 0.2.1

- Fixed invalid author email in package metadata (`rg@local`) that made
  PyPI reject the 0.2.0 upload outright. No other changes - see 0.2.0 below
  for everything actually shipped in this release.

## 0.2.0

Generalized from an Antigravity-only tool into a mesh any harness can join,
plus a round of security and reliability fixes found through live multi-harness
testing (agy, pi, opencode) and a 4-way collaborative review.

- `wait` now returns every queued unread message in one call via a per-session
  cursor, instead of only the latest one, and no longer replays old history on
  first use.
- A per-session file lock rejects a second concurrent `wait` instead of racing
  it, so a harness that spawns a new background call without closing the old
  one can no longer double-deliver a message.
- The peer auth token is now actually enforced during the socket handshake —
  previously it was checked but never gated on, so any frame was accepted
  regardless of authentication.
- Session name and engine are auto-detected from the calling harness (agy,
  `pi`, `opencode`, ...) by walking the parent-process chain, instead of
  defaulting to a hardcoded `antigravity`. `--name` / `--sender` /
  `$AGENT_PEER_NAME` still take priority when given.
- Sessions register with their real working directory instead of a hardcoded
  `~/projects`.
- Desktop notification clicks now activate the actual target app instead of
  always opening Script Editor (a quirk of how `osascript` notifications get
  attributed on macOS).
- `wait` warns instead of silently proceeding when there's no live listener to
  receive replies.
- Inbox, cursor, and lock files are now owner-only (`chmod 0600`/`0700`);
  previously they were created with the default umask.
- The unread cursor now survives `agent-peer inbox --clear` (reset, not
  deleted) and no longer treats a corrupted cursor file as "everything already
  read" — it falls back to replaying the backlog instead of silently dropping it.
- A session's status resets to `idle` once its queued messages are read;
  previously it stayed on `new-msg` forever after the first message.
- Ships a ready-to-install `SKILL.md` + README per harness under
  [`skills/`](./skills) (agy, pi, opencode), so a fresh clone doesn't have to
  reconstruct them from scratch.
- `agent-peer status` for agy/Claude Code quota and context numbers (see
  [`docs/status.md`](./docs/status.md)).

## 0.1.0

Initial release: a local Unix Domain Socket IPC mesh unlocking Claude Code's
native `/peer` protocol for external agents.

- `agent-peer list` / `send` / `listen` / `inbox` / `logs` / `watch`.
- `agent-peer wait` for reactive, poll-free wakeup.
- Per-session inbox isolation, session name collision resolution, and
  AGY/Claude engine badges in `list` and `logs`.
