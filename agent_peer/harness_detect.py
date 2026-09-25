"""Pure harness detection for `agent-peer setup` (docs/tasks/0022).

One function per supported harness returning `(installed, evidence)`.
Detection only - never installs anything. All paths resolve against the
`home` argument (default: current `$HOME`) at call time so tests can pass an
isolated dir; nothing is bound at import.
"""

import os
import shutil

# Install targets below are the confirmed paths from 0022's table - do not
# invent new ones. Each entry: id, label, target SKILL.md relative to $HOME,
# and the repo-bundled source dir under skills/.
HARNESSES = (
    {
        "id": "agy",
        "label": "agy (Antigravity)",
        "target": os.path.join(".gemini", "config", "skills", "agent-peer", "SKILL.md"),
        "rules_target": os.path.join(".gemini", "GEMINI.md"),
    },
    {
        "id": "codex",
        "label": "Codex CLI",
        "target": os.path.join(".codex", "skills", "agent-peer", "SKILL.md"),
        "rules_target": os.path.join(".codex", "AGENTS.md"),
    },
    {
        "id": "muse",
        "label": "muse",
        "target": os.path.join(".agents", "skills", "agent-peer", "SKILL.md"),
        "rules_target": os.path.join(".agents", "AGENTS.md"),
    },
    {
        "id": "pi",
        "label": "pi / oh-my-pi (omp)",
        "target": os.path.join(".pi", "agent", "skills", "agent-peer", "SKILL.md"),
        "rules_target": os.path.join(".pi", "agent", "AGENTS.md"),
    },
    {
        "id": "opencode",
        "label": "opencode",
        "target": os.path.join(".config", "opencode", "skills", "agent-peer", "SKILL.md"),
        "rules_target": os.path.join(".config", "opencode", "AGENTS.md"),
    },
    {
        "id": "claude",
        "label": "Claude Code",
        "target": os.path.join(".claude", "skills", "agent-peer", "SKILL.md"),
        "rules_target": os.path.join(".claude", "CLAUDE.md"),
    },
)

_HARNESS_IDS = tuple(h["id"] for h in HARNESSES)


def _home_dir(home=None):
    return os.path.expanduser("~") if home is None else home


def _which_or_dir(binaries, dirs, home):
    """Shared probe: first matching binary path or existing dir wins."""
    if isinstance(binaries, str):
        binaries = (binaries,)
    for binary in binaries:
        found = shutil.which(binary)
        if found:
            return True, found
    for rel in dirs:
        candidate = os.path.join(home, rel)
        if os.path.exists(candidate):
            return True, candidate
    return False, ""


def detect_agy(home=None):
    return _which_or_dir("agy", (".gemini",), _home_dir(home))


def detect_codex(home=None):
    return _which_or_dir("codex", (".codex",), _home_dir(home))


def detect_muse(home=None):
    return _which_or_dir("muse", (os.path.join(".local", "share", "muse"),), _home_dir(home))


def detect_pi(home=None):
    return _which_or_dir(("pi", "omp"), (".pi",), _home_dir(home))


def detect_opencode(home=None):
    return _which_or_dir("opencode", (os.path.join(".config", "opencode"),), _home_dir(home))


def detect_claude(home=None):
    # No documented env var marks "inside Claude Code", so ~/.claude existing
    # covers both the in-harness and standalone-CLI cases from 0022's table.
    return _which_or_dir("claude", (".claude",), _home_dir(home))


_DETECTORS = {
    "agy": detect_agy,
    "codex": detect_codex,
    "muse": detect_muse,
    "pi": detect_pi,
    "opencode": detect_opencode,
    "claude": detect_claude,
}


def skill_source_path(harness_id, _package="agent_peer"):
    """Repo-bundled `skills/<id>/SKILL.md` for a harness.

    Prefers `importlib.resources` (works inside a pip/uv wheel) and falls back
    to the repo checkout (editable/dev runs where the data dir isn't present).
    Returns the path only if the file exists, else None.
    """
    if harness_id not in _DETECTORS:
        raise KeyError(f"unknown harness: {harness_id!r}")
    filename = "SKILL.md"
    try:
        from importlib import resources

        # os.fspath raises TypeError when not backed by a real filesystem.
        path = os.fspath(resources.files(_package).joinpath("skills", harness_id, filename))
        if os.path.isfile(path):
            return path
    except Exception:
        pass
    fallback = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", harness_id, filename
    )
    return fallback if os.path.isfile(fallback) else None


def target_path(harness_id, home=None):
    """Where the harness's SKILL.md should be installed under `home`."""
    for harness in HARNESSES:
        if harness["id"] == harness_id:
            return os.path.join(_home_dir(home), harness["target"])
    raise KeyError(f"unknown harness: {harness_id!r}")


def rules_target_path(harness_id, home=None):
    """Where the harness's global rule file should be updated under `home`."""
    for harness in HARNESSES:
        if harness["id"] == harness_id:
            return os.path.join(_home_dir(home), harness["rules_target"])
    raise KeyError(f"unknown harness: {harness_id!r}")


def detect_all(home=None):
    """Detect every supported harness. Returns a list of dicts with keys
    id, label, target, installed, evidence, skill_present, source,
    rules_target, rules_present."""
    home = _home_dir(home)
    entries = []
    for harness in HARNESSES:
        hid = harness["id"]
        installed, evidence = _DETECTORS[hid](home)
        target = os.path.join(home, harness["target"])
        rules_target = os.path.join(home, harness["rules_target"])
        source = skill_source_path(hid)
        from agent_peer.setup_rules import has_marker_block

        entries.append(
            {
                "id": hid,
                "label": harness["label"],
                "target": target,
                "installed": installed,
                "evidence": evidence or "not detected",
                "skill_present": os.path.isfile(target),
                "source": source,
                "rules_target": rules_target,
                "rules_present": has_marker_block(rules_target),
            }
        )
    return entries
