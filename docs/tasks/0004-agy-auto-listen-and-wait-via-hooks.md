# 0004 - agy auto-bootstraps listen and auto-launches background wait via hooks

**Status:** paused
**Owner:** `agent-peer-e4`
**Files:** `skills/agy/hooks/agent-peer-hooks.py`, `.agents/hooks.json` (currently disabled -
`"enabled": false`)

## Why this exists

agy's real hooks.json format and event contract are documented in
`~/.gemini/antigravity-cli/builtin/skills/agy-customizations/docs/hooks.md` (shipped with the CLI
itself - read directly, not guessed). The only events that actually exist are `PreToolUse`,
`PostToolUse`, `PreInvocation`, `PostInvocation`, and `Stop` - there is no `SessionStart` or
`AfterAgent` (an earlier version of this task assumed those names from a web search; they don't
exist in the real schema).

Hooks cannot wake agy from true idle on their own - they only fire on activity (a tool call, an
invocation, a stop event). The thing that actually achieves reactive wakeup, proven live for 2
days straight in `ottoshare-factory`, is `agent-peer wait` launched by the model itself as its own
`run_command` (tracked in agy's own `/tasks`) - when that task exits, agy's harness resumes the
model. This task is a convenience improvement (remove reliance on the model remembering to launch
it), not a reliability fix - the manual pattern already works.

## What was built and verified live

1. **`PreInvocation` bootstraps `listen`.** Real bug found and fixed: `invocationNum` is
   **0-indexed** (the first call of a fresh session is `0`, not `1` as first assumed). Session name
   is derived from `conversationId` (stable across a session), not auto-detection - a hook
   subprocess's ancestry doesn't resolve to "agy", so auto-detection generates a different name
   every single invocation.
2. **`Stop` does a synchronous, bounded inbox check** (`agent-peer wait --name <name> --timeout
   12`, hook's own configured timeout raised to 20s) and returns `{"decision": "continue", "reason":
   "..."}` when a message is waiting - this uses `Stop`'s real, documented injection contract.
   Confirmed live end-to-end: a message sent to the hook-registered session showed up in agy's
   conversation and it replied back correctly.

## Why it's paused, not done

The live test also surfaced a real design problem: after receiving the injected message, agy fell
back to its own existing skill instruction and launched `agent-peer wait` **itself** as a
`run_command` background task - which is good (that's the real mechanism), but it auto-detects a
**different session name** than the one this hook registered it under (`agy-hook-<conversationId>`
vs whatever auto-detection gives a genuine in-session tool call). Two different identities for one
physical session means messages can land in either inbox unpredictably.

The safer fix identified but not yet built: have `PreInvocation` inject an `ephemeralMessage` (a
documented, non-executing injection type) telling the model its stable agent-peer session name once,
so its own `agent-peer wait --name <name>` calls target the same identity the hook registered.

A riskier alternative was also discussed - inject a `toolCall` (`run_command`, `CommandLine:
"agent-peer wait --name <name>"`, `WaitMsBeforeAsync: 1000`) via hooks to make the hook itself
launch a real tracked background task - the `WaitMsBeforeAsync` flag was found in this project's own
`~/.gemini/config/skills/agent-peer/SKILL.md`, so it's real, but whether an injected toolCall
actually becomes a tracked `/tasks` entry the way a model-initiated one does is unverified. The
owner judged this not safe enough to try yet.

**Paused here, hook disabled (`"enabled": false"` in `.agents/hooks.json`) rather than removed** -
the bootstrap half works cleanly, only the identity-consistency half needs the ephemeral-name fix
before this is trustworthy enough to leave on.

## Acceptance

The owner re-enables the hook (`"enabled": true` in `.agents/hooks.json`) only once the
ephemeral-name-injection fix (or another design that resolves the identity mismatch) is built and
tested live, with confidence it won't produce two inconsistent session identities for one agy
session.

## Hand-walk

<empty>
