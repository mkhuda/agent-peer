#!/usr/bin/env python3
"""agy lifecycle hooks for agent-peer - experimental, see docs/tasks/0004.
PreInvocation (first turn only): bootstrap 'listen'.
Stop (fullyIdle only): auto-launch a background 'wait'.
Every invocation is logged to ~/.agent-peer/agy-hook.log for debugging."""

import json
import os
import shutil
import subprocess
import sys
import time
import traceback

LOG_PATH = os.path.expanduser("~/.agent-peer/agy-hook.log")

# Common install locations to fall back to if 'agent-peer' isn't on the
# hook subprocess's PATH - agy may run hooks with a restricted environment.
FALLBACK_BIN_PATHS = [
    os.path.expanduser("~/.local/bin/agent-peer"),
    os.path.expanduser("~/.local/share/uv/tools/agent-peer/bin/agent-peer"),
]


def log(line):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
    except Exception:
        pass  # logging must never be what breaks the hook


def emit(payload=None):
    print(json.dumps(payload or {}))


def agent_peer_bin():
    found = shutil.which("agent-peer")
    if found:
        return found
    for candidate in FALLBACK_BIN_PATHS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "agent-peer"  # last resort, will fail loudly and get logged


def session_name(payload):
    # Stable across every hook invocation of the same agy session, unlike
    # PID-based auto-detection - each hook call is a separate subprocess
    # whose ancestry doesn't resolve to 'agy', so auto-detect would otherwise
    # generate a DIFFERENT generic name every single time it's invoked.
    conv_id = payload.get("conversationId") or "unknown"
    return f"agy-hook-{conv_id[:8]}"


def workspace_cwd(payload):
    paths = payload.get("workspacePaths") or []
    return paths[0] if paths else None


def spawn_detached(args, out_name, cwd=None):
    out_path = os.path.expanduser(f"~/.agent-peer/agy-hook-{out_name}.log")
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        out_file = open(out_path, "a", encoding="utf-8")
    except Exception:
        out_file = subprocess.DEVNULL
    subprocess.Popen(
        args,
        stdout=out_file,
        stderr=out_file,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=cwd,
    )


def handle_pre_invocation(payload):
    # agy's invocationNum is 0-indexed - the first call of a fresh session is 0.
    num = payload.get("invocationNum")
    if num != 0:
        log(f"pre-invocation: skipped (invocationNum={num!r}, not 0)")
        return None
    bin_path = agent_peer_bin()
    name = session_name(payload)
    cwd = workspace_cwd(payload)
    log(f"pre-invocation: invocationNum=0, spawning '{bin_path} listen --name {name}' (cwd={cwd})")
    spawn_detached([bin_path, "listen", "--name", name], "listen", cwd=cwd)
    return None


# Kept comfortably under the hook's own 'timeout' in hooks.json (20s) so the
# subprocess itself is killed cleanly before agy's hook runtime would kill us.
STOP_WAIT_TIMEOUT_S = 12


def handle_stop(payload):
    idle = payload.get("fullyIdle")
    if not idle:
        log(f"stop: skipped (fullyIdle={idle!r})")
        return None

    bin_path = agent_peer_bin()
    name = session_name(payload)
    log(f"stop: fullyIdle=True, checking inbox synchronously for '{name}' (timeout={STOP_WAIT_TIMEOUT_S}s)")

    try:
        result = subprocess.run(
            [bin_path, "wait", "--name", name, "--timeout", str(STOP_WAIT_TIMEOUT_S)],
            capture_output=True,
            text=True,
            timeout=STOP_WAIT_TIMEOUT_S + 5,
        )
    except subprocess.TimeoutExpired:
        log("stop: 'agent-peer wait' subprocess itself timed out - treating as no message")
        return None

    log(f"stop: wait exit={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}")

    if result.returncode != 0:
        # "Timeout waiting for message." - nothing arrived in the window, let
        # agy stop normally.
        return None

    # cmd_wait prints one '📬 [NEW MESSAGE from X]: content' line per message.
    return {
        "decision": "continue",
        "reason": f"agent-peer: new peer message(s) arrived -\n{result.stdout.strip()}",
    }


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    raw = sys.stdin.read()
    log(f"invoked: event={event!r} argv={sys.argv[1:]!r} stdin={raw!r}")

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        log(f"error parsing stdin JSON: {e}")
        payload = {}

    result = None
    try:
        if event == "pre-invocation":
            result = handle_pre_invocation(payload)
        elif event == "stop":
            result = handle_stop(payload)
        else:
            log(f"unknown event {event!r}, no-op")
    except Exception:
        log("EXCEPTION:\n" + traceback.format_exc())

    log(f"emitting: {result!r}")
    emit(result)


if __name__ == "__main__":
    main()
