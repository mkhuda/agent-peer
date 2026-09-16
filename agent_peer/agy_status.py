"""Reads the agy/Antigravity quota + context snapshot bridged over from its
statusline hook (see ~/.gemini/antigravity-cli/statusline.sh), then freshens
the 5h quota numbers with a live, on-demand call (see agy_live.py) since
that's the one piece worth being right-now-accurate for a continue-or-handoff
decision. Weekly quota and context window have no API source at all — agy
computes/reports those itself — so they always come from the cache."""

import os
import time
from datetime import datetime, timezone
import json

from . import agy_live
from .protocol import AGENT_PEER_DIR

AGY_STATUS_FILE = os.path.join(AGENT_PEER_DIR, "agy_status.json")

STALE_AFTER_SECONDS = 15 * 60  # agy only writes this when it actually renders a statusline

# Raw payload keys -> the short, JSON-friendly names used in the trimmed dict.
_QUOTA_KEYS = {
    "gemini_5h": "gemini-5h",
    "gemini_weekly": "gemini-weekly",
    "claude_gpt_5h": "3p-5h",
    "claude_gpt_weekly": "3p-weekly",
}


def read_agy_status():
    """Returns (payload: dict | None, age_seconds: float | None, error: str | None)."""
    if not os.path.exists(AGY_STATUS_FILE):
        return None, None, "No agy status captured yet — open an interactive `agy` session at least once."
    try:
        with open(AGY_STATUS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return None, None, f"Could not read {AGY_STATUS_FILE}: {e}"

    captured_at = data.get("captured_at")
    age_seconds = None
    if captured_at:
        try:
            dt = datetime.strptime(captured_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age_seconds = time.time() - dt.timestamp()
        except ValueError:
            pass

    return data.get("payload"), age_seconds, None


def _fmt_age(seconds):
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    return f"{seconds / 3600:.1f}h ago"


def fmt_duration(seconds):
    if seconds is None:
        return "?"
    seconds = int(seconds)
    h, m = seconds // 3600, (seconds % 3600) // 60
    return f"{h}h{m}m" if h else f"{m}m"


def get_agy_status_dict() -> dict:
    """The trimmed, JSON-friendly shape — every remaining_pct here means the
    same thing (higher = more headroom), so this can be compared directly
    against get_claude_status_dict()'s quota without re-deriving anything."""
    payload, age_seconds, error = read_agy_status()
    if error:
        return {"error": error}

    ctx = payload.get("context_window") or {}
    raw_quota = payload.get("quota") or {}

    quota = {}
    for short_name, raw_key in _QUOTA_KEYS.items():
        entry = raw_quota.get(raw_key)
        quota[short_name] = (
            {
                "remaining_pct": round((entry.get("remaining_fraction") or 0) * 100, 1),
                "resets_in_s": entry.get("reset_in_seconds"),
                "source": "cached",
            }
            if entry
            else None
        )

    # The 5h numbers are the ones most likely to matter for a right-now
    # decision — freshen just those with a live call. Weekly + context have
    # no API source (see agy_live.py's docstring), so they stay cache-only.
    live, live_error = agy_live.fetch_live_5h_quota()
    if live:
        for key in ("gemini_5h", "claude_gpt_5h"):
            if live.get(key):
                quota[key] = {**live[key], "source": "live"}

    return {
        "email": payload.get("email"),
        "plan": payload.get("plan_tier"),
        "model": (payload.get("model") or {}).get("display_name"),
        "snapshot_age_s": round(age_seconds, 1) if age_seconds is not None else None,
        "live_5h_fetch_error": live_error,
        "context": {
            "used_pct": ctx.get("used_percentage", 0),
            "input_tokens": ctx.get("total_input_tokens", 0),
            "output_tokens": ctx.get("total_output_tokens", 0),
            "window_size": ctx.get("context_window_size", 0),
        },
        "quota": quota,
    }


def format_agy_status(as_json: bool = False) -> str:
    status = get_agy_status_dict()
    if "error" in status:
        return status["error"]
    if as_json:
        return json.dumps(status, indent=2)

    age_s = status["snapshot_age_s"]
    ctx = status["context"]
    lines = [
        f"agy status  (email: {status['email'] or '?'} · plan: {status['plan'] or '?'} · model: {status['model'] or '?'})",
        f"  snapshot age: {_fmt_age(age_s)}"
        + ("  ⚠ stale (agy not run recently)" if (age_s or 0) > STALE_AFTER_SECONDS else ""),
        "",
        f"  context: {ctx['used_pct']:.1f}% used "
        f"({ctx['input_tokens']} in / {ctx['output_tokens']} out / {ctx['window_size']} window)"
        "  [cached, no live source]",
        "",
        "  quota:",
    ]
    labels = {
        "gemini_5h": "gemini   5h  ",
        "gemini_weekly": "gemini   week",
        "claude_gpt_5h": "claude/gpt 5h",
        "claude_gpt_weekly": "claude/gpt wk",
    }
    for key, label in labels.items():
        q = status["quota"].get(key)
        if not q:
            lines.append(f"    {label}  n/a")
            continue
        tag = "live" if q.get("source") == "live" else "cached"
        lines.append(
            f"    {label}  {q['remaining_pct']:>5.1f}% remaining  (resets in {fmt_duration(q['resets_in_s'])})  [{tag}]"
        )
    if status.get("live_5h_fetch_error"):
        lines.append(f"\n  ⚠ live 5h refresh failed, showing cached numbers instead: {status['live_5h_fetch_error']}")
    return "\n".join(lines)
