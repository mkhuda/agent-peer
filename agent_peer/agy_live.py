"""On-demand fetch of agy's 5-hour quota via Google's internal
fetchAvailableModels API. See docs/status.md for the full rationale."""

from __future__ import annotations

import base64
import json
import re
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


def _tokenize_model_hint(display_name: str | None) -> list[str]:
    """'Gemini 3.8 Flash (Medium)' -> ['3', '8', 'flash'] - digits and words,
    dropping stopwords too short/generic to disambiguate (e.g. bare 'gemini')."""
    if not display_name:
        return []
    words = re.findall(r"[a-zA-Z0-9]+", display_name.lower())
    return [w for w in words if w not in ("gemini", "medium", "high", "low", "fast")]


def _matches_active_model(model_key: str, tokens: list[str]) -> bool:
    if not tokens:
        return False
    key = model_key.lower()
    return all(t in key for t in tokens)


def _pick_5h_quota(data: dict, active_model_hint: str | None) -> dict:
    """Pure selection logic, split out from fetch_live_5h_quota so it's
    testable without a real network call or Keychain access.

    active_model_hint is the display name of the model the caller is actually
    on right now (e.g. from the cached statusline snapshot) - used to pick
    that exact model's quota out of the response instead of blindly taking
    the minimum across every model Google happens to return. Without a
    confident match, a model with no quotaInfo at all (not "0% remaining" -
    genuinely absent) must NOT be masked by an unrelated sibling model that
    still reports 100% because the caller simply isn't using it."""
    tokens = _tokenize_model_hint(active_model_hint)

    # Per provider: prefer the model matching the caller's active model by
    # name; only fall back to the most-constrained-of-everything heuristic
    # when nothing matches the hint at all (no hint, or hint not present in
    # this response) - never as a way to paper over the active model's own
    # quotaInfo being absent.
    buckets: dict[str, list[dict]] = {"gemini_5h": [], "claude_gpt_5h": []}
    matched: dict[str, dict] = {}
    active_present_without_quota: dict[str, bool] = {"gemini_5h": False, "claude_gpt_5h": False}
    for model_key, model in (data.get("models") or {}).items():
        provider = model.get("apiProvider", "")
        if provider == _GEMINI_PROVIDER:
            bucket_key = "gemini_5h"
        elif provider in _THIRD_PARTY_PROVIDERS:
            bucket_key = "claude_gpt_5h"
        else:
            continue

        quota_info = model.get("quotaInfo")
        is_active = _matches_active_model(model_key, tokens)

        if not quota_info:
            if is_active:
                active_present_without_quota[bucket_key] = True
            continue

        entry = {
            "remaining_pct": round((quota_info.get("remainingFraction") or 0) * 100, 1),
            "resets_in_s": _reset_in_seconds(quota_info.get("resetTime")),
        }
        buckets[bucket_key].append(entry)
        if is_active and bucket_key not in matched:
            matched[bucket_key] = entry

    result = {}
    for key, entries in buckets.items():
        if key in matched:
            result[key] = matched[key]
        elif active_present_without_quota[key]:
            # The active model showed up but Google isn't tracking its quota
            # here - an unrelated sibling model's number would be a guess
            # dressed up as a live reading. Say so instead of overriding.
            result[key] = None
        else:
            result[key] = min(entries, key=lambda e: e["remaining_pct"]) if entries else None
    return result


def fetch_live_5h_quota(active_model_hint: str | None = None) -> tuple[dict | None, str | None]:
    """Returns ({"gemini_5h": {...} | None, "claude_gpt_5h": {...} | None}, None)
    on a successful call, or (None, error) if anything went wrong. See
    _pick_5h_quota for how active_model_hint is used to select the result."""
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

    return _pick_5h_quota(data, active_model_hint), None
