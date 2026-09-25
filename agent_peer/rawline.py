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
        # Cursor position within self.lines[-1] only - editing an earlier
        # continuation line was never supported (backspace/append already
        # only ever touched the last line), so this doesn't expand that.
        self.cursor_col = 0

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
        self.cursor_col = 0

    def append_char(self, ch: str):
        """Inserts at the cursor, not always the end - a char typed after
        moving left with the arrow keys must land where the cursor is."""
        line = self.lines[-1]
        self.lines[-1] = line[: self.cursor_col] + ch + line[self.cursor_col :]
        self.cursor_col += len(ch)

    def backspace(self):
        """Deletes the char immediately before the cursor, not always the
        last char of the line - same reasoning as append_char."""
        if self.cursor_col > 0:
            line = self.lines[-1]
            self.lines[-1] = line[: self.cursor_col - 1] + line[self.cursor_col :]
            self.cursor_col -= 1
        elif len(self.lines) > 1:
            self.lines.pop()
            self.cursor_col = len(self.lines[-1])

    def move_left(self):
        self.cursor_col = max(0, self.cursor_col - 1)

    def move_right(self):
        self.cursor_col = min(len(self.lines[-1]), self.cursor_col + 1)

    def move_word_left(self):
        """Skip trailing whitespace then the word before it - the usual
        Option/Alt+Left convention, not just a single character hop."""
        line, i = self.lines[-1], self.cursor_col
        while i > 0 and line[i - 1].isspace():
            i -= 1
        while i > 0 and not line[i - 1].isspace():
            i -= 1
        self.cursor_col = i

    def move_word_right(self):
        line, i = self.lines[-1], self.cursor_col
        n = len(line)
        while i < n and line[i].isspace():
            i += 1
        while i < n and not line[i].isspace():
            i += 1
        self.cursor_col = i

    def newline(self):
        self.lines.append("")
        self.cursor_col = 0

    def paste_extend(self, pasted_text: str):
        """Inserts a (possibly multi-line) pasted block at the cursor - text
        already typed after the cursor is preserved as the tail of the last
        pasted line, matching how append_char/backspace already respect
        cursor position. Never submits on its own; the whole paste still
        lands in the compose box for an explicit Enter."""
        pasted_text = pasted_text.replace("\r\n", "\n").replace("\r", "\n")
        paste_lines = pasted_text.split("\n")
        line = self.lines[-1]
        head, tail = line[: self.cursor_col], line[self.cursor_col :]
        self.lines[-1] = head + paste_lines[0]
        for extra in paste_lines[1:]:
            self.lines.append(extra)
        self.lines[-1] += tail
        self.cursor_col = len(self.lines[-1]) - len(tail)

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
        then clears everything below and reprints. Finishes by moving the
        terminal's real cursor back to cursor_col if it isn't at the end of
        the last line - editing a wrapped line in the middle is still only
        approximate (same limitation _visual_rows already has), but the
        common single-row case lands exactly."""
        self.clear_rendered_area()
        sys.stdout.write(self.prompt + self.lines[0])
        for extra in self.lines[1:]:
            sys.stdout.write("\n" + CONTINUATION_PREFIX + extra)
        trailing = len(self.lines[-1]) - self.cursor_col
        if trailing > 0:
            sys.stdout.write(f"\x1b[{trailing}D")
        sys.stdout.flush()
        self._printed_visual_rows = self._visual_rows()


_WORD_MODIFIERS = ("3", "5", "9")  # Alt, Ctrl, and Alt+Ctrl-ish CSI modifier codes seen in the wild

def _read_escape_sequence(fd, timeout=0.05):
    """Called right after a bare ESC byte. Returns "ENTER" for Alt+Enter
    (\\x1b\\r) or CSI-u Shift+Enter (\\x1b[13;2u), "PASTE_START" for a
    bracketed-paste begin marker (\\x1b[200~), "CANCEL" for a standalone
    Esc (nothing follows), "LEFT"/"RIGHT" for a plain arrow key (\\x1b[D /
    \\x1b[C), "WORD_LEFT"/"WORD_RIGHT" for a modified arrow (Option/Alt or
    Ctrl + Left/Right, e.g. \\x1b[1;3D or \\x1b[1;5D) or the classic
    readline meta convention (Alt+b / Alt+f, i.e. \\x1bb / \\x1bf), None for
    any other/unrecognized sequence."""
    import select

    if not select.select([fd], [], [], timeout)[0]:
        return "CANCEL"
    ch2 = os.read(fd, 1).decode(errors="replace")
    if ch2 in ("\r", "\n"):  # ICRNL may have already translated \r to \n
        return "ENTER"
    if ch2 == "b":  # Alt+b: readline-style "word back"
        return "WORD_LEFT"
    if ch2 == "f":  # Alt+f: readline-style "word forward"
        return "WORD_RIGHT"
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
    if seq == "D":
        return "LEFT"
    if seq == "C":
        return "RIGHT"
    if seq.endswith("D") and any(f";{m}" in seq for m in _WORD_MODIFIERS):
        return "WORD_LEFT"
    if seq.endswith("C") and any(f";{m}" in seq for m in _WORD_MODIFIERS):
        return "WORD_RIGHT"
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
                    editor.render()
                elif marker == "PASTE_START":
                    editor.paste_extend(_consume_bracketed_paste(fd))
                    editor.render()
                elif marker == "CANCEL":
                    editor.reset()
                    editor.render()
                elif marker == "LEFT":
                    editor.move_left()
                    editor.render()
                elif marker == "RIGHT":
                    editor.move_right()
                    editor.render()
                elif marker == "WORD_LEFT":
                    editor.move_word_left()
                    editor.render()
                elif marker == "WORD_RIGHT":
                    editor.move_word_right()
                    editor.render()
                continue  # any other unrecognized sequence: ignore, keep composing
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
            ch2 = msvcrt.getwch()  # extended-key second byte
            if ch2 == "K":  # Left
                editor.move_left()
                editor.render()
            elif ch2 == "M":  # Right
                editor.move_right()
                editor.render()
            elif ch2 == "s":  # Ctrl+Left
                editor.move_word_left()
                editor.render()
            elif ch2 == "t":  # Ctrl+Right
                editor.move_word_right()
                editor.render()
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
