"""Direct, on-demand fetch of agy's 5-hour quota window from Google's
internal Cloud Code Assist API (`fetchAvailableModels`) — the same call
`agy` itself makes.

Deliberately narrow in scope and behavior:
- Only the 5h window: `fetchAvailableModels`'s response carries just one
  quota window per model, matching the 5h reset time. There's no weekly
  figure in it (checked directly) and no context-window usage either (agy
  computes that locally, not via any API) — those stay cache-only, from the
  statusline bridge (see agy_status.py).
- Only called on demand, once per `agent-peer status` invocation — never a
  background timer. This hits an undocumented `v1internal` endpoint not
  published for third-party use, so the safe usage shape is "ride along with
  an explicit check", not autonomous polling.
- Never retries and never raises: any failure returns (None, reason) and the
  caller falls back to the (possibly stale) cached numbers instead. A string
  of failures should make a human stop calling this, not spin a retry loop
  that keeps hammering the endpoint.
"""

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

    # Collect every model's entry per provider group, then keep the *most
    # constrained* one (lowest remainingFraction) — not just the first match.
    # Some models in each group are quota-exempt (remainingFraction=1,
    # resetTime=None, e.g. internal preview/tab models) and would otherwise
    # get picked arbitrarily depending on dict order, silently reporting
    # "100%, no reset" for a pool that's actually partially used.
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
