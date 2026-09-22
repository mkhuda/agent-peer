"""Background update notice for agent-peer (docs/tasks/0022, Step 5-6).

Cache-first: the PyPI JSON endpoint is only hit when the cache is older than
TTL, so most invocations are a pure file read with zero network. Every
failure mode (offline, PyPI down, corrupt cache) is silent - a background
version check must never surface a network error. Notify only, never
auto-upgrade.
"""

import json
import os
import sys
import time
import urllib.request

PYPI_URL = "https://pypi.org/pypi/agent-peer/json"
TTL_SECONDS = 24 * 3600
REQUEST_TIMEOUT = 2.5


def cache_path(home=None):
    home = os.path.expanduser("~") if home is None else home
    return os.path.join(home, ".agent-peer", "update_check.json")


def _parse_version(text):
    """'1.2.3' -> (1, 2, 3); non-numeric segments stop the parse."""
    parts = []
    for segment in str(text).strip().split("."):
        digits = ""
        for char in segment:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(latest, current):
    try:
        return _parse_version(latest) > _parse_version(current)
    except Exception:
        return False


def _fetch_latest(timeout=REQUEST_TIMEOUT):
    with urllib.request.urlopen(PYPI_URL, timeout=timeout) as response:
        payload = json.load(response)
    return payload["info"]["version"]


def get_latest_version(home=None, ttl=TTL_SECONDS, timeout=REQUEST_TIMEOUT):
    """Newest known version: fresh fetch when the cache is stale, cached
    value otherwise, None when nothing is known (offline first run, etc)."""
    path = cache_path(home)
    cached = {}
    try:
        with open(path, encoding="utf-8") as fh:
            cached = json.load(fh)
    except Exception:
        cached = {}
    stale = (
        not isinstance(cached, dict)
        or not cached.get("latest_version")
        or time.time() - float(cached.get("last_checked", 0)) > ttl
    )
    if not stale:
        return cached["latest_version"]
    try:
        latest = _fetch_latest(timeout)
    except Exception:
        return cached.get("latest_version")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"last_checked": time.time(), "latest_version": latest}, fh)
    except Exception:
        pass
    return latest


def maybe_notify(current_version, home=None):
    """One-line stderr notice when a newer release is known. Never stdout
    (keeps --json/piped output clean), never in non-interactive contexts,
    never raises."""
    try:
        if not sys.stdout.isatty():
            return False
        latest = get_latest_version(home)
        if latest and is_newer(latest, current_version):
            print(
                f"agent-peer {latest} available (you have {current_version})"
                " - see CHANGELOG.md or run your install method's upgrade command",
                file=sys.stderr,
            )
            return True
        return False
    except Exception:
        return False
