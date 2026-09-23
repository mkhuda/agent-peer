"""A session file with valid JSON plus trailing garbage must still show up -
the real bug behind a native Claude Code session (PID 19231,
example-project-98) vanishing from `list`/`send` (codex-8763, 2026-09-23).

Confirmed live: Claude Code's own binary (v2.1.280) wrote a session file
ending in a literal extra '}' - not an agent-peer write, and not a race
(reproduced consistently, 5/5, with no concurrent writer)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import isolated_home, run_cli


def test_session_with_trailing_garbage_still_appears_in_list():
    with isolated_home() as home:
        sessions_dir = os.path.join(home, ".claude", "sessions")
        os.makedirs(sessions_dir, exist_ok=True)

        pid = os.getpid()
        session = {
            "name": "example-project-98",
            "cwd": "/Users/redacted/projects/example-project",
            "status": "idle",
            "messagingSocketPath": f"/tmp/cc-socks/{pid}.sock",
        }
        raw = json.dumps(session) + "}"  # the real corruption: one stray extra '}'
        with open(os.path.join(sessions_dir, f"{pid}.json"), "w", encoding="utf-8") as f:
            f.write(raw)
        key_path = os.path.join(sessions_dir, f"{pid}.{'a' * 64}.key")
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"peerToken": "test-token"}))

        result = run_cli(["list"], home)
        assert result.returncode == 0, result.stderr
        assert "example-project-98" in result.stdout, result.stdout
