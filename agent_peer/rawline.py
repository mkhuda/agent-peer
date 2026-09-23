"""Raw/cbreak multi-line input for `join` - termios on POSIX, msvcrt on
Windows, self-owned by construction (not delegated to whatever readline
implementation happens to be linked, which varies unpredictably - macOS
ships libedit, not GNU readline, and their key-binding behavior differs).
Falls back to plain input() when stdin isn't a real tty (tests, pipes) -
same guard shape as picker.py's isatty() check."""

import os
import shutil
import sys

from . import compat

CONTINUATION_PREFIX = "... "


class LineEditor:
    """Holds the in-progress (possibly multi-line) composed buffer, so a
    background thread (the live-message poller) can redraw it after
    printing an incoming message without owning the read loop itself."""

    def __init__(self, prompt="> "):
        self.prompt = prompt
        self.lines = [""]
        self._printed_visual_rows = 0

    def _visual_rows(self) -> int:
        """Physical terminal rows the current buffer occupies, accounting
        for the terminal soft-wrapping a line wider than its column count -
        clear_rendered_area() must move the cursor up past ALL of these, not
        just past explicit newlines, or a long line leaves behind a trail of
        duplicated prompts (a real bug, caught live: every keystroke past
        the wrap point kept printing a new physical row instead of
        overwriting the wrapped one)."""
        cols = shutil.get_terminal_size((80, 24)).columns or 80
        total = 0
        for i, line in enumerate(self.lines):
            prefix_len = len(self.prompt) if i == 0 else len(CONTINUATION_PREFIX)
            total += max(1, -(-(prefix_len + len(line)) // cols))  # ceiling division
        return total

    def text(self) -> str:
        return "\n".join(self.lines)

    def reset(self):
        self.lines = [""]

    def append_char(self, ch: str):
        self.lines[-1] += ch

    def backspace(self):
        if self.lines[-1]:
            self.lines[-1] = self.lines[-1][:-1]
        elif len(self.lines) > 1:
            self.lines.pop()

    def newline(self):
        self.lines.append("")

    def paste_extend(self, pasted_text: str):
        """Inserts a (possibly multi-line) pasted block into the buffer -
        its first line joins whatever was already typed, each subsequent
        line becomes its own continuation. Never submits on its own; the
        whole paste still lands in the compose box for an explicit Enter."""
        pasted_text = pasted_text.replace("\r\n", "\n").replace("\r", "\n")
        paste_lines = pasted_text.split("\n")
        self.lines[-1] += paste_lines[0]
        for extra in paste_lines[1:]:
            self.newline()
            self.lines[-1] = extra

    def clear_rendered_area(self):
        """Erases whatever this editor last drew (cursor up past every
        physical row it occupied - explicit newlines AND soft-wrapped ones -
        then clear to end of screen), without printing anything back - for a
        caller that wants to print something permanent (a sent message's own
        card) in that same screen space instead."""
        if self._printed_visual_rows > 1:
            sys.stdout.write(f"\033[{self._printed_visual_rows - 1}A")
        sys.stdout.write("\r\033[J")
        sys.stdout.flush()
        self._printed_visual_rows = 0

    def render(self):
        """Redraws the whole composition from its own top line - moves the
        cursor up past every physical row printed by the previous render,
        then clears everything below and reprints."""
        self.clear_rendered_area()
        sys.stdout.write(self.prompt + self.lines[0])
        for extra in self.lines[1:]:
            sys.stdout.write("\n" + CONTINUATION_PREFIX + extra)
        sys.stdout.flush()
        self._printed_visual_rows = self._visual_rows()


def _read_escape_sequence(fd, timeout=0.05):
    """Called right after a bare ESC byte. Returns "ENTER" for Alt+Enter
    (\\x1b\\r) or CSI-u Shift+Enter (\\x1b[13;2u), "PASTE_START" for a
    bracketed-paste begin marker (\\x1b[200~), None for a standalone Esc or
    any other/unrecognized sequence (e.g. arrow keys - ignored for now)."""
    import select

    if not select.select([fd], [], [], timeout)[0]:
        return None
    ch2 = os.read(fd, 1).decode(errors="replace")
    if ch2 in ("\r", "\n"):  # ICRNL may have already translated \r to \n
        return "ENTER"
    if ch2 != "[":
        return None
    seq = ""
    while select.select([fd], [], [], timeout)[0]:
        c = os.read(fd, 1).decode(errors="replace")
        seq += c
        if c.isalpha() or c == "~":
            break
    if seq == "13;2u":
        return "ENTER"
    if seq == "200~":
        return "PASTE_START"
    return None


def _consume_bracketed_paste(fd, timeout=2.0) -> str:
    """Called right after a PASTE_START marker - reads literal bytes until
    the matching \\x1b[201~ end marker, returning everything in between.
    A generous overall timeout is the safety net if a terminal ever sends
    a start marker without a matching end (should not happen in practice)."""
    import select
    import time

    end_marker = "\x1b[201~"
    buf = ""
    deadline = time.time() + timeout
    while not buf.endswith(end_marker):
        if not select.select([fd], [], [], 0.2)[0]:
            if time.time() > deadline:
                break
            continue
        buf += os.read(fd, 4096).decode(errors="replace")
    if buf.endswith(end_marker):
        buf = buf[: -len(end_marker)]
    return buf


def _read_posix(editor: LineEditor):
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        # setcbreak leaves ISIG on - Ctrl+C would be intercepted by the
        # kernel as a real SIGINT instead of reaching us as byte 0x03,
        # which is how we handle it (as data, to return None cleanly).
        # It also leaves ICRNL on, which silently rewrites \r to \n before
        # we ever see it - harmless for a single Enter keypress (checked
        # for both anyway), but doubles into a stray blank line for every
        # \r\n pasted from a Windows clipboard (\r\n -> \n\n). Clearing it
        # gets us the real bytes so paste_extend's own CRLF normalization
        # actually has something to do.
        mode = termios.tcgetattr(fd)
        mode[0] &= ~termios.ICRNL
        mode[3] &= ~termios.ISIG
        termios.tcsetattr(fd, termios.TCSANOW, mode)
        sys.stdout.write("\x1b[?2004h")  # ask the terminal to wrap pastes in \x1b[200~..\x1b[201~
        sys.stdout.flush()
        while True:
            ch = os.read(fd, 1).decode(errors="replace")
            if ch == "\x03":  # Ctrl+C
                return None
            if ch == "\x04":  # Ctrl+D
                if editor.text() == "":
                    return None
                continue  # mid-composition EOF - ignored, not a submit
            if ch == "\x1b":
                marker = _read_escape_sequence(fd)
                if marker == "ENTER":
                    editor.newline()
                elif marker == "PASTE_START":
                    editor.paste_extend(_consume_bracketed_paste(fd))
                else:
                    editor.reset()
                editor.render()
                continue
            if ch in ("\r", "\n"):
                if editor.lines[-1].endswith("\\"):
                    editor.lines[-1] = editor.lines[-1][:-1]
                    editor.newline()
                    editor.render()
                    continue
                return editor.text()
            if ch in ("\x7f", "\x08"):
                editor.backspace()
                editor.render()
                continue
            if ch.isprintable():
                editor.append_char(ch)
                editor.render()
    finally:
        sys.stdout.write("\x1b[?2004l")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_windows(editor: LineEditor):
    import msvcrt

    while True:
        ch = msvcrt.getwch()
        if ch == "\x03":
            return None
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()  # swallow the special-key second byte (arrows etc.) - not handled yet
            continue
        if ch == "\x1b":
            editor.reset()
            editor.render()
            continue
        if ch in ("\r", "\n"):
            if editor.lines[-1].endswith("\\"):
                editor.lines[-1] = editor.lines[-1][:-1]
                editor.newline()
                editor.render()
                continue
            return editor.text()
        if ch == "\x08":
            editor.backspace()
            editor.render()
            continue
        if ch.isprintable():
            editor.append_char(ch)
            editor.render()


def read_message(editor: LineEditor):
    """Blocks for one (possibly multi-line, `\\`-continued) composed
    message using the given, already-shared LineEditor - assumes a real
    tty (raw/cbreak read); the caller decides the non-tty fallback (see
    join.py, which needs plain input() for its own piped-stdin tests).
    Returns the text on Enter, or None on Ctrl+C/Ctrl+D (leave)."""
    editor.reset()
    editor.render()
    reader = _read_windows if compat.IS_WINDOWS else _read_posix
    return reader(editor)
