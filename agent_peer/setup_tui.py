"""Interactive `agent-peer setup`: checkbox picker + skill file install.

`pick_harnesses` is the only terminal-dependent part - curses on POSIX,
msvcrt on Windows (no extra dependency either way). `install_skills`/
`remove_skills` are plain file copies/deletes so tests drive them directly.
"""

import os
import shutil
import sys

from agent_peer import compat, harness_detect


def install_skills(harness_ids, home=None):
    """Copy each harness's bundled SKILL.md to its install target.

    Returns {"installed": [...], "skipped": [...]} where skipped names a
    harness whose bundled source is missing (never a silent no-op - the
    caller prints both lists).
    """
    installed, skipped = [], []
    for hid in harness_ids:
        source = harness_detect.skill_source_path(hid)
        if source is None:
            skipped.append(hid)
            continue
        target = harness_detect.target_path(hid, home)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
        installed.append(hid)
    return {"installed": installed, "skipped": skipped}


def remove_skills(harness_ids, home=None):
    """Delete installed SKILL.md files (plus the now-empty skill dir)."""
    removed, missing = [], []
    for hid in harness_ids:
        target = harness_detect.target_path(hid, home)
        if not os.path.isfile(target):
            missing.append(hid)
            continue
        os.remove(target)
        try:
            os.rmdir(os.path.dirname(target))
        except OSError:
            pass
        removed.append(hid)
    return {"removed": removed, "missing": missing}


def _initial_checked(entries):
    # Pre-check detected harnesses AND ones already installed (Step 7: a
    # re-run reflects current state, not just fresh detection).
    return {e["id"] for e in entries if e["installed"] or e["skill_present"]}


def _entry_line(entry, checked):
    mark = "[x]" if entry["id"] in checked else "[ ]"
    state = entry["evidence"] if entry["installed"] else "not detected - check to install anyway"
    if entry["skill_present"]:
        state += " (skill already installed)"
    return f"{mark} {entry['label']}: {state}"


def _pick_harnesses_windows(entries):
    import msvcrt

    os.system("")  # touches the console mode, enabling ANSI on legacy conhost
    checked = _initial_checked(entries)
    cursor = 0
    sys.stdout.write("\x1b[?25l")
    try:
        while True:
            sys.stdout.write("\x1b[H\x1b[J")
            print("agent-peer setup - Space toggles, Enter confirms, a/n all/none, q quits\n")
            for i, entry in enumerate(entries):
                prefix = "> " if i == cursor else "  "
                print(prefix + _entry_line(entry, checked))
            sys.stdout.flush()
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                ch2 = msvcrt.getwch()
                if ch2 == "H":
                    cursor = (cursor - 1) % len(entries)
                elif ch2 == "P":
                    cursor = (cursor + 1) % len(entries)
            elif ch == " ":
                checked.symmetric_difference_update({entries[cursor]["id"]})
            elif ch == "\r":
                return set(checked)
            elif ch in ("q", "\x1b"):
                return _initial_checked(entries)
            elif ch == "a":
                checked.update(e["id"] for e in entries)
            elif ch == "n":
                checked.clear()
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def pick_harnesses(entries):
    """Checkbox picker (curses on POSIX, msvcrt on Windows). Returns the
    selected harness ids. Space toggles, Enter confirms, a/n select-all/none,
    q quits keeping the initial state. Raises RuntimeError with no TTY.
    """
    if not sys.stdin.isatty():
        raise RuntimeError("no TTY on stdin - run `agent-peer setup --all` instead")
    if compat.IS_WINDOWS:
        return _pick_harnesses_windows(entries)
    try:
        import curses
    except ImportError as exc:
        raise RuntimeError(f"curses unavailable on this platform: {exc}") from exc

    checked = _initial_checked(entries)

    def _run(stdscr):
        curses.curs_set(0)
        cursor = 0
        while True:
            stdscr.clear()
            stdscr.addstr(0, 0, "agent-peer setup - Space toggles, Enter confirms, a/n all/none, q quits")
            for i, entry in enumerate(entries):
                line = _entry_line(entry, checked)
                attr = curses.A_REVERSE if i == cursor else curses.A_NORMAL
                stdscr.addstr(i + 2, 0, line[: curses.COLS - 1], attr)
            key = stdscr.getch()
            if key in (ord(" "),):
                hid = entries[cursor]["id"]
                checked.symmetric_difference_update({hid})
            elif key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
                return set(checked)
            elif key in (ord("q"), 27):
                return _initial_checked(entries)
            elif key == ord("a"):
                checked.update(e["id"] for e in entries)
            elif key == ord("n"):
                checked.clear()
            elif key == curses.KEY_UP:
                cursor = (cursor - 1) % len(entries)
            elif key == curses.KEY_DOWN:
                cursor = (cursor + 1) % len(entries)

    return curses.wrapper(_run)
