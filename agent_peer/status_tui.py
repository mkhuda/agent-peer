"""Hand-rolled ANSI live dashboard for `agent-peer status --live` - no TUI
library, keeps agent-peer zero-dependency. See docs/tasks/0017."""

import shutil
import sys
import time

from .logs import RESET, BRIGHT_RED, GREEN, DIM, supports_color
from .agy_status import fmt_duration, get_agy_status_dict
from .claude_status import get_claude_status_dict
from .codex_status import get_codex_status_dict

BAR_FILLED = "━"
BAR_EMPTY = "─"
LOW_REMAINING_PCT = 20  # matches ~/.claude/statusline.js's own red-at-<=20% threshold


def _bar(remaining_pct: float, width: int, use_color: bool) -> str:
    filled = max(0, min(width, round(width * remaining_pct / 100)))
    bar = BAR_FILLED * filled + BAR_EMPTY * (width - filled)
    if not use_color:
        return bar
    color = BRIGHT_RED if remaining_pct <= LOW_REMAINING_PCT else GREEN
    return f"{color}{bar}{RESET}"


def _pct_str(remaining_pct: float, use_color: bool) -> str:
    text = f"{remaining_pct:>5.1f}%"
    if not use_color or remaining_pct > LOW_REMAINING_PCT:
        return text
    return f"{BRIGHT_RED}{text}{RESET}"


def _agy_rows(status: dict):
    labels = {
        "gemini_5h": "gemini 5h",
        "gemini_weekly": "gemini week",
        "claude_gpt_5h": "claude/gpt 5h",
        "claude_gpt_weekly": "claude/gpt wk",
    }
    rows = []
    for key, label in labels.items():
        q = (status.get("quota") or {}).get(key)
        if q:
            rows.append((label, q["remaining_pct"], q["resets_in_s"]))
    return rows


def _claude_rows(status: dict):
    rows = []
    for key, q in (status.get("quota") or {}).items():
        label = {"session_5h": "session 5h", "weekly_all": "weekly all"}.get(key, key.removeprefix("weekly_"))
        rows.append((label, q["remaining_pct"], q["resets_in_s"]))
    return rows


def _codex_rows(status: dict):
    rows = []
    for key, q in (status.get("quota") or {}).items():
        period = "5h" if key.endswith("_5h") else "week"
        rows.append((f"{q['label']} {period}", q["remaining_pct"], q["resets_in_s"]))
    return rows


_SECTIONS = [
    ("agy (Antigravity)", get_agy_status_dict, _agy_rows),
    ("claude (Claude Code)", get_claude_status_dict, _claude_rows),
    ("codex (Codex CLI)", get_codex_status_dict, _codex_rows),
]


def _render_frame(providers, use_color: bool, interval: float) -> str:
    cols = shutil.get_terminal_size(fallback=(80, 24)).columns
    label_w = 16
    bar_w = max(10, min(30, cols - label_w - 22))
    lines = []
    for title, fetch, rows_fn in _SECTIONS:
        if providers is not None and title.split(" ")[0] not in providers:
            continue
        header = f"── {title} " + "─" * max(0, cols - len(title) - 4)
        lines.append(f"{DIM}{header}{RESET}" if use_color else header)
        status = fetch()
        if "error" in status:
            lines.append(f"  {status['error']}")
            lines.append("")
            continue
        for label, remaining_pct, resets_in_s in rows_fn(status):
            bar = _bar(remaining_pct, bar_w, use_color)
            pct = _pct_str(remaining_pct, use_color)
            lines.append(f"  {label:<{label_w}} {bar}  {pct}  resets {fmt_duration(resets_in_s)}")
        lines.append("")
    footer = f"refreshing every {int(interval)}s - Ctrl-C to exit"
    lines.append(f"{DIM}{footer}{RESET}" if use_color else footer)
    return "\n".join(lines)


def run_live(providers, interval: float = 20.0, no_color: bool = False):
    """Blocks, redrawing in place until Ctrl-C. Caller must confirm stdout is a TTY first."""
    use_color = not no_color and supports_color()
    hide_cursor = "\x1b[?25l"
    show_cursor = "\x1b[?25h"
    home_clear = "\x1b[H\x1b[J"
    sys.stdout.write(hide_cursor)
    sys.stdout.flush()
    try:
        while True:
            frame = _render_frame(providers, use_color, interval)
            sys.stdout.write(home_clear + frame + "\n")
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(show_cursor)
        sys.stdout.flush()
