"""On-demand fetch of agy's 5-hour quota via Google's internal
fetchAvailableModels API. See docs/status.md for the full rationale."""

from __future__ import annotations

import base64
import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone

QUOTA_API = "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"
KEYCHAIN_PREFIX = "go-keyring-base64:"

# Required so the backend recognizes the caller as Antigravity — without
# these, Gemini's own numbers still come back but Claude/GPT models 404.
_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Antigravity/1.0.0 Chrome/138.0.7204.235 Electron/37.3.1 Safari/537.36"
    ),
    "Client-Metadata": '{"ideType":"ANTIGRAVITY","platform":"WINDOWS","pluginType":"GEMINI"}',
}

_GEMINI_PROVIDER = "API_PROVIDER_GOOGLE_GEMINI"
_THIRD_PARTY_PROVIDERS = {"API_PROVIDER_ANTHROPIC_VERTEX", "API_PROVIDER_OPENAI_VERTEX"}


def _read_agy_access_token() -> str | None:
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if raw.returncode != 0 or not raw.stdout.strip():
            return None
        val = raw.stdout.strip()
        token_raw = (
            base64.b64decode(val[len(KEYCHAIN_PREFIX):]).decode("utf-8")
            if val.startswith(KEYCHAIN_PREFIX)
            else val
        )
        return json.loads(token_raw)["token"]["access_token"]
    except Exception:
        return None


def _reset_in_seconds(reset_time: str | None) -> int | None:
    if not reset_time:
        return None
    try:
        dt = datetime.strptime(reset_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))


def fetch_live_5h_quota() -> tuple[dict | None, str | None]:
    """Returns ({"gemini_5h": {...} | None, "claude_gpt_5h": {...} | None}, None)
    on a successful call, or (None, error) if anything went wrong."""
    access_token = _read_agy_access_token()
    if not access_token:
        return None, "No agy OAuth token found in keychain."

    req = urllib.request.Request(
        QUOTA_API,
        data=b"{}",
        headers={**_HEADERS, "Authorization": f"Bearer {access_token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return None, f"fetchAvailableModels failed: HTTP {e.code}"
    except Exception as e:
        return None, f"fetchAvailableModels failed: {e}"

    # Keep the most-constrained model per group (lowest remainingFraction) -
    # a quota-exempt model reports 100%/no-reset, masking a partially-used pool.
    buckets: dict[str, list[dict]] = {"gemini_5h": [], "claude_gpt_5h": []}
    for model in (data.get("models") or {}).values():
        quota_info = model.get("quotaInfo")
        if not quota_info:
            continue
        entry = {
            "remaining_pct": round((quota_info.get("remainingFraction") or 0) * 100, 1),
            "resets_in_s": _reset_in_seconds(quota_info.get("resetTime")),
        }
        provider = model.get("apiProvider", "")
        if provider == _GEMINI_PROVIDER:
            buckets["gemini_5h"].append(entry)
        elif provider in _THIRD_PARTY_PROVIDERS:
            buckets["claude_gpt_5h"].append(entry)

    result = {
        key: (min(entries, key=lambda e: e["remaining_pct"]) if entries else None)
        for key, entries in buckets.items()
    }
    return result, None
