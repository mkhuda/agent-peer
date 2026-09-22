"""Reads Codex CLI's own usage/quota by driving `codex app-server` over stdio
JSON-RPC. See docs/tasks/0016 for the research trail this mirrors."""

import json
import queue
import subprocess
import threading
import time
from typing import Optional

APP_SERVER_CMD = ["codex", "app-server"]
_TIMEOUT_S = 10


def _call_app_server(calls: list) -> tuple:
    """Runs one app-server session: initialize, then each (method, params) call in
    order. Returns ({method: result}, None) or (None, error)."""
    try:
        proc = subprocess.Popen(
            APP_SERVER_CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
    except FileNotFoundError:
        return None, "codex CLI not found on PATH."

    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(l) for l in proc.stdout], daemon=True).start()

    def send(msg_id, method, params):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}) + "\n")
        proc.stdin.flush()

    def send_notification(method, params):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
        proc.stdin.flush()

    def recv_for_id(want_id):
        # The server also pushes unsolicited notifications on this same stream
        # (e.g. remoteControl/status/changed) - match by id, skip everything else.
        deadline = time.time() + _TIMEOUT_S
        while time.time() < deadline:
            try:
                line = lines.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                return None
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                return msg
        return None

    try:
        send(0, "initialize", {"clientInfo": {"name": "agent-peer", "version": "0.1"}})
        init = recv_for_id(0)
        if not init or "result" not in init:
            return None, "codex app-server did not respond to initialize."
        send_notification("initialized", {})

        results = {}
        for i, (method, params) in enumerate(calls, start=1):
            send(i, method, params)
            resp = recv_for_id(i)
            if not resp:
                return None, f"{method} timed out."
            if "error" in resp:
                return None, f"{method} failed: {resp['error']}"
            results[method] = resp.get("result")
        return results, None
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()


def fetch_codex_rate_limits() -> tuple:
    """Returns (result: dict | None, error: str | None) for account/rateLimits/read."""
    results, error = _call_app_server([("account/rateLimits/read", {})])
    if error:
        return None, error
    return results.get("account/rateLimits/read"), None


def _window_label(mins: Optional[int]) -> Optional[str]:
    if mins is None:
        return None
    return "5h" if mins <= 360 else "weekly"


def _reset_in_seconds(resets_at: Optional[int]) -> Optional[int]:
    if not resets_at:
        return None
    return max(0, int(resets_at - time.time()))


def get_codex_status_dict() -> dict:
    """JSON shape normalized to remaining_pct, one entry per rate-limit window
    across every bucket in rateLimitsByLimitId (codex, base_model_inference aka
    'gpt-reserve' i.e. the TUI's "Luna Reserve", ...)."""
    data, error = fetch_codex_rate_limits()
    if error:
        return {"error": error}

    buckets = data.get("rateLimitsByLimitId") or {}
    if not buckets and data.get("rateLimits"):
        buckets = {data["rateLimits"].get("limitId", "codex"): data["rateLimits"]}

    quota = {}
    for limit_id, bucket in buckets.items():
        label = bucket.get("limitName") or bucket.get("normalModelSlug") or limit_id
        for window_key in ("primary", "secondary"):
            window = bucket.get(window_key)
            if not window:
                continue
            period = _window_label(window.get("windowDurationMins"))
            if not period:
                continue
            quota[f"{limit_id}_{period}"] = {
                "label": label,
                "remaining_pct": round(100 - (window.get("usedPercent") or 0), 1),
                "resets_in_s": _reset_in_seconds(window.get("resetsAt")),
            }

    return {
        "plan": (data.get("rateLimits") or {}).get("planType"),
        "quota": quota,
    }


def format_codex_status(as_json: bool = False) -> str:
    from .agy_status import fmt_duration

    status = get_codex_status_dict()
    if "error" in status:
        return status["error"]
    if as_json:
        return json.dumps(status, indent=2)

    lines = [f"codex status  (plan: {status['plan'] or '?'})", "", "  quota:"]
    for key, q in status["quota"].items():
        period = "5h  " if key.endswith("_5h") else "week"
        lines.append(
            f"    {q['label']:<14} {period}  {q['remaining_pct']:>5.1f}% remaining  (resets in {fmt_duration(q['resets_in_s'])})"
        )
    return "\n".join(lines)
