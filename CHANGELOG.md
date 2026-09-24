# Changelog

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
