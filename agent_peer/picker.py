"""Generic checkbox picker - curses on POSIX, msvcrt on Windows, no extra
dependency either way. A parallel of setup_tui.py's own picker, kept
separate on purpose: that one is shipped and hand-walked, and its entry
shape (harness id/installed/evidence) doesn't fit a different domain."""

import os
import sys

from . import compat


def _pick_multi_windows(items, title, render_item_fn, get_id_fn, preselected):
    import msvcrt

    os.system("")  # touches the console mode, enabling ANSI on legacy conhost
    checked = set(preselected)
    cursor = 0
    sys.stdout.write("\x1b[?25l")
    try:
        while True:
            sys.stdout.write("\x1b[H\x1b[J")
            print(title)
            print("[Space] toggle  [Enter] confirm  [a] all  [n] none  [q] cancel\n")
            for i, item in enumerate(items):
                mark = "[x]" if get_id_fn(item) in checked else "[ ]"
                prefix = "> " if i == cursor else "  "
                print(f"{prefix}{mark} {render_item_fn(item)}")
            sys.stdout.flush()
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                ch2 = msvcrt.getwch()
                if ch2 == "H":
                    cursor = (cursor - 1) % len(items)
                elif ch2 == "P":
                    cursor = (cursor + 1) % len(items)
            elif ch == " ":
                checked.symmetric_difference_update({get_id_fn(items[cursor])})
            elif ch == "\r":
                return checked
            elif ch in ("q", "\x1b"):
                return set(preselected)
            elif ch == "a":
                checked.update(get_id_fn(i) for i in items)
            elif ch == "n":
                checked.clear()
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def pick_multi(items, title, render_item_fn, get_id_fn, preselected=None):
    """Checkbox picker over an arbitrary item list. `render_item_fn(item)`
    returns its display line, `get_id_fn(item)` its stable id. Returns the
    selected ids, or `preselected` unchanged on cancel (q/Esc)."""
    if not items:
        return set()
    if not sys.stdin.isatty():
        raise RuntimeError("no TTY on stdin")
    preselected = set(preselected or [])
    if compat.IS_WINDOWS:
        return _pick_multi_windows(items, title, render_item_fn, get_id_fn, preselected)
    try:
        import curses
    except ImportError as exc:
        raise RuntimeError(f"curses unavailable on this platform: {exc}") from exc

    checked = set(preselected)

    def _run(stdscr):
        curses.curs_set(0)
        cursor = 0
        while True:
            stdscr.clear()
            stdscr.addstr(0, 0, title)
            stdscr.addstr(1, 0, "[Space] toggle  [Enter] confirm  [a] all  [n] none  [q] cancel")
            for i, item in enumerate(items):
                mark = "[x]" if get_id_fn(item) in checked else "[ ]"
                line = f"{mark} {render_item_fn(item)}"
                attr = curses.A_REVERSE if i == cursor else curses.A_NORMAL
                stdscr.addstr(i + 3, 0, line[: curses.COLS - 1], attr)
            key = stdscr.getch()
            if key == ord(" "):
                checked.symmetric_difference_update({get_id_fn(items[cursor])})
            elif key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
                return set(checked)
            elif key in (ord("q"), 27):
                return set(preselected)
            elif key == ord("a"):
                checked.update(get_id_fn(i) for i in items)
            elif key == ord("n"):
                checked.clear()
            elif key == curses.KEY_UP:
                cursor = (cursor - 1) % len(items)
            elif key == curses.KEY_DOWN:
                cursor = (cursor + 1) % len(items)

    return curses.wrapper(_run)
