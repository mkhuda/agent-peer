"""Fetches Claude Code's own usage/quota from Anthropic's API using its
OAuth token from the macOS Keychain. See docs/status.md for the rationale."""

import getpass
import hashlib
import json
import os
import subprocess
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
DEFAULT_KEYCHAIN_SERVICE = "Claude Code-credentials"
CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"


def _hashed_keychain_service(config_dir: str) -> str:
    """Mirrors Claude Code's own hashing of CLAUDE_CONFIG_DIR - must hash the
    exact exported string, not a resolved path, or it names an empty keychain item."""
    normalized = unicodedata.normalize("NFC", config_dir)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{DEFAULT_KEYCHAIN_SERVICE}-{digest}"


def _active_keychain_services() -> list[str]:
    """Which keychain service holds this environment's active credential,
    following the same CLAUDE_CONFIG_DIR resolution order Claude Code itself uses."""
    secure_env = os.environ.get("CLAUDE_SECURESTORAGE_CONFIG_DIR")
    if secure_env is not None:
        return [DEFAULT_KEYCHAIN_SERVICE] if not secure_env else [_hashed_keychain_service(secure_env)]

    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if not config_dir:
        return [DEFAULT_KEYCHAIN_SERVICE]
    return [_hashed_keychain_service(config_dir), DEFAULT_KEYCHAIN_SERVICE]


def _read_keychain(service: str) -> dict | None:
    try:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser()
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", user, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if raw.returncode == 0 and raw.stdout.strip():
            return json.loads(raw.stdout).get("claudeAiOauth")
    except Exception:
        pass
    return None


def _read_credentials() -> dict | None:
    for service in _active_keychain_services():
        oauth = _read_keychain(service)
        if oauth:
            return oauth
    try:
        return json.loads(CREDENTIALS_FILE.read_text()).get("claudeAiOauth")
    except (OSError, json.JSONDecodeError):
        return None


def _refresh(refresh_token: str) -> str | None:
    body = json.dumps(
        {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": CLIENT_ID}
    ).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode()).get("access_token")
    except Exception:
        return None


def _request_usage(access_token: str) -> dict:
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "agent-peer/0.1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _fetch_email(access_token: str) -> str | None:
    """Whose token this is — confirms which active account got read, rather
    than trusting the keychain-slot resolution silently."""
    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/profile",
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": "agent-peer/0.1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            account = json.loads(resp.read().decode()).get("account") or {}
            return account.get("email")
    except Exception:
        return None


def fetch_claude_usage():
    """Returns (usage: dict | None, meta: dict | None, error: str | None)."""
    oauth = _read_credentials()
    if not oauth or not oauth.get("accessToken"):
        return None, None, "No Claude Code credentials found (keychain or ~/.claude/.credentials.json)."

    access_token = oauth["accessToken"]
    try:
        data = _request_usage(access_token)
    except urllib.error.HTTPError as e:
        if e.code == 401 and oauth.get("refreshToken"):
            fresh = _refresh(oauth["refreshToken"])
            if not fresh:
                return None, None, "Access token expired and refresh failed."
            access_token = fresh
            try:
                data = _request_usage(access_token)
            except Exception as e2:
                return None, None, f"Usage fetch failed after refresh: {e2}"
        else:
            return None, None, f"Usage fetch failed: HTTP {e.code}"
    except Exception as e:
        return None, None, f"Usage fetch failed: {e}"

    meta = {"subscription_type": oauth.get("subscriptionType"), "email": _fetch_email(access_token)}
    return data, meta, None


def _reset_in_seconds(resets_at: str | None) -> int | None:
    if not resets_at:
        return None
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(resets_at)
    except ValueError:
        return None
    return max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))


def get_claude_status_dict() -> dict:
    """JSON shape normalized to remaining_pct, flipping Anthropic's raw
    utilization% and dropping unreleased-feature placeholder fields."""
    data, meta, error = fetch_claude_usage()
    if error:
        return {"error": error}

    def window(entry: dict) -> dict:
        used_pct = entry.get("utilization") or 0
        return {
            "remaining_pct": round(100 - used_pct, 1),
            "resets_in_s": _reset_in_seconds(entry.get("resets_at")),
        }

    quota = {
        "session_5h": window(data.get("five_hour") or {}),
        "weekly_all": window(data.get("seven_day") or {}),
    }
    for lim in data.get("limits") or []:
        if lim.get("kind") != "weekly_scoped":
            continue
        model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
        if not model:
            continue
        quota[f"weekly_{model.lower()}"] = window({"utilization": lim.get("percent"), "resets_at": lim.get("resets_at")})

    return {
        "email": meta.get("email"),
        "plan": meta.get("subscription_type"),
        "quota": quota,
    }


def format_claude_status(as_json: bool = False) -> str:
    from .agy_status import fmt_duration

    status = get_claude_status_dict()
    if "error" in status:
        return status["error"]
    if as_json:
        return json.dumps(status, indent=2)

    lines = [f"claude status  (email: {status['email'] or '?'} · plan: {status['plan'] or '?'})", "", "  quota:"]
    labels = {"session_5h": "session   5h  ", "weekly_all": "weekly    all "}
    for key, q in status["quota"].items():
        label = labels.get(key, f"weekly    {key.removeprefix('weekly_'):<5}")
        lines.append(f"    {label} {q['remaining_pct']:>5.1f}% remaining  (resets in {fmt_duration(q['resets_in_s'])})")
    return "\n".join(lines)
