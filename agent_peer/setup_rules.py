"""Global rules injection and managed marker block engine (Task 0036 Phase 1).

Injects and removes bounded mesh discipline blocks in harness-global rule files
(~/.codex/AGENTS.md, ~/.claude/CLAUDE.md, ~/.gemini/GEMINI.md, ~/.agents/AGENTS.md,
~/.pi/agent/AGENTS.md, ~/.config/opencode/AGENTS.md).
"""

import os
import re
from agent_peer import harness_detect

DEFAULT_MARKER_ID = "agent-peer mesh discipline"

CANONICAL_RULES_CONTENT = """## IPC Mesh Discipline (agent-peer)
All sessions participate in the local IPC mesh (`agent-peer`):
- **Turn 1 Check:** Run `agent-peer list` to discover peers; start `agent-peer listen` if not reachable.
- **Standby (Non-Native):** Non-native harnesses (Antigravity, Muse, Pi, OpenCode) have no native socket push. Keep a background waiter (`agent-peer wait` or `agent-peer thread <id>`) active during peer handoffs (wake behavior varies by host runtime).
- **Zero Parked Active (Codex):** Codex must never end a turn parked active (`left: false`) without an active waiter. Use peek & post (`--timeout 10`) or continuous opt-in (`--timeout 10 --follow`). Run `--leave` when departing or disabling follow-all.
- **Thread Discipline (Claude):** Run shared threads under monitor; run `--leave` when stepping out."""


def _get_markers(marker_id: str):
    start_marker = f"# >>> {marker_id} >>>"
    end_marker = f"# <<< {marker_id} <<<"
    return start_marker, end_marker


def _valid_block_regex(marker_id: str):
    start_marker, end_marker = _get_markers(marker_id)
    return re.compile(
        r"(?:^|\n)[ \t]*" + re.escape(start_marker) + r"[ \t]*\n.*?\n[ \t]*" + re.escape(end_marker) + r"[ \t]*(?:\n|$)",
        re.DOTALL,
    )


def has_marker_block(target_path, marker_id: str = DEFAULT_MARKER_ID) -> bool:
    """Return True if target_path exists and contains a complete, correctly ordered managed marker block."""
    target_path = str(target_path)
    if not os.path.isfile(target_path):
        return False
    try:
        with open(target_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        return bool(_valid_block_regex(marker_id).search(content))
    except OSError:
        return False


def _clean_orphan_line(line: str, marker: str, replacement: str = None) -> str:
    """Helper to clean or replace an orphan marker on a line.
    If line is purely marker (with surrounding whitespace), replaces or drops the line.
    If marker is embedded within other text, replaces only the marker substring.
    """
    stripped = line.strip()
    if stripped == marker:
        return replacement if replacement is not None else ""
    # Marker embedded inline with other text
    if replacement is not None:
        return line.replace(marker, replacement.strip())
    return line.replace(marker, "")


def inject_marker_block(target_path, content: str, marker_id: str = DEFAULT_MARKER_ID) -> bool:
    """Inject or idempotently update a marker-bounded block in target_path.

    Preserves any surrounding content. Creates parent directories and file if needed.
    Safely recovers from malformed/orphan marker lines without eating user content.
    """
    start_marker, end_marker = _get_markers(marker_id)
    block_text = f"{start_marker}\n{content.strip()}\n{end_marker}\n"

    target_path = str(target_path)
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)

    if not os.path.exists(target_path):
        with open(target_path, "w", encoding="utf-8") as fh:
            fh.write(block_text)
        return True

    with open(target_path, "r", encoding="utf-8") as fh:
        existing = fh.read()

    match = _valid_block_regex(marker_id).search(existing)
    if match:
        # Match starts either at ^ or \n
        start_pos = match.start()
        end_pos = match.end()
        # Preserve prefix up to match start
        prefix = existing[:start_pos]
        # If match started at \n, preserve that leading \n in prefix if needed
        if start_pos > 0 and existing[start_pos] == "\n":
            prefix += "\n"
        suffix = existing[end_pos:]
        new_text = prefix + block_text + suffix
    else:
        # If there are orphan/reversed/inline markers, remove/clean them first, then append clean block
        cleaned = existing
        if start_marker in cleaned:
            lines = cleaned.splitlines(keepends=True)
            cleaned = "".join(_clean_orphan_line(line, start_marker) for line in lines)
        if end_marker in cleaned:
            lines = cleaned.splitlines(keepends=True)
            cleaned = "".join(_clean_orphan_line(line, end_marker) for line in lines)

        if cleaned and not cleaned.endswith("\n\n"):
            separator = "\n" if cleaned.endswith("\n") else "\n\n"
        else:
            separator = ""
        new_text = cleaned + separator + block_text

    with open(target_path, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    return True


def remove_marker_block(target_path, marker_id: str = DEFAULT_MARKER_ID) -> bool:
    """Remove a marker-bounded block from target_path, leaving user content intact.

    Returns True if content was modified, False otherwise.
    Safely handles orphan, inline, or reversed markers without consuming user content.
    """
    target_path = str(target_path)
    if not os.path.isfile(target_path):
        return False

    with open(target_path, "r", encoding="utf-8") as fh:
        existing = fh.read()

    start_marker, end_marker = _get_markers(marker_id)
    if _valid_block_regex(marker_id).search(existing):
        pattern = re.compile(
            r"(?:(\n)?[ \t]*)?" + re.escape(start_marker) + r"[ \t]*\n.*?\n[ \t]*" + re.escape(end_marker) + r"[ \t]*(\n)?",
            re.DOTALL,
        )
        new_text = pattern.sub(r"\1", existing, count=1)
        new_text = re.sub(r"\n{3,}", "\n\n", new_text)
    else:
        new_text = existing
        if start_marker in new_text:
            lines = new_text.splitlines(keepends=True)
            new_text = "".join(_clean_orphan_line(line, start_marker) for line in lines)
        if end_marker in new_text:
            lines = new_text.splitlines(keepends=True)
            new_text = "".join(_clean_orphan_line(line, end_marker) for line in lines)

    if new_text == existing:
        return False

    with open(target_path, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    return True


def install_rules(harness_ids, home=None):
    """Inject canonical mesh rules into global rule files for each harness ID."""
    installed, skipped = [], []
    for hid in harness_ids:
        try:
            target = harness_detect.rules_target_path(hid, home)
        except KeyError:
            skipped.append(hid)
            continue
        inject_marker_block(target, CANONICAL_RULES_CONTENT)
        installed.append(hid)
    return {"installed": installed, "skipped": skipped}


def remove_rules(harness_ids, home=None):
    """Remove canonical mesh rules from global rule files for each harness ID."""
    removed, missing = [], []
    for hid in harness_ids:
        try:
            target = harness_detect.rules_target_path(hid, home)
        except KeyError:
            missing.append(hid)
            continue
        if remove_marker_block(target):
            removed.append(hid)
        else:
            missing.append(hid)
    return {"removed": removed, "missing": missing}
