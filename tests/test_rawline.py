"""rawline.py's raw/cbreak multi-line editor - verified against a REAL
pseudo-terminal (not just checked for exceptions), since terminal input
behavior (ICRNL translation, ISIG signal interception) only shows up under
an actual pty, not a piped stdin. Two real bugs were caught this way before
landing: Alt+Enter mis-detected as a plain Esc-clear because ICRNL can
translate \\r to \\n before the byte ever reaches us, and Ctrl+C crashing
the process instead of returning None because ISIG was left enabled (the
kernel intercepted it as a real SIGINT instead of delivering byte 0x03)."""

import os
import pty
import select
import signal
import sys
import time

import pytest

from agent_peer.rawline import LineEditor

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD_SCRIPT = f"""
import sys
sys.path.insert(0, {REPO_ROOT!r})
from agent_peer.rawline import LineEditor, read_message
sys.stderr.write("CHILD READY\\n"); sys.stderr.flush()
editor = LineEditor("> ")
result = read_message(editor)
sys.stdout.write("\\nRESULT:" + repr(result) + "\\n")
sys.stdout.flush()
"""


def _run_pty(write_sequence, timeout=6):
    """Spawns a real pty, waits for the child to actually reach cbreak mode
    (not a fixed sleep - a race with import time is exactly how this test
    was flaky before), sends `write_sequence`, and returns the captured
    output text."""
    master, slave = pty.openpty()
    pid = os.fork()
    if pid == 0:
        os.close(master)
        os.setsid()
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        os.close(slave)
        os.execvp(sys.executable, [sys.executable, "-c", _CHILD_SCRIPT])
    else:
        os.close(slave)
        try:
            buf = b""
            deadline = time.time() + timeout
            while b"CHILD READY" not in buf and time.time() < deadline:
                if select.select([master], [], [], 0.2)[0]:
                    buf += os.read(master, 4096)
            time.sleep(0.7)  # settle time for tty.setcbreak() to actually engage

            for item in write_sequence:
                os.write(master, item)
                time.sleep(0.08)
            time.sleep(0.5)

            out = b""
            try:
                while select.select([master], [], [], 0.4)[0]:
                    chunk = os.read(master, 4096)
                    if not chunk:
                        break
                    out += chunk
            except OSError:
                pass

            for _ in range(30):
                wpid, _status = os.waitpid(pid, os.WNOHANG)
                if wpid != 0:
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            return out.decode(errors="replace")
        finally:
            os.close(master)


def test_visual_rows_counts_soft_wrap_not_just_explicit_newlines(monkeypatch):
    """Real bug, caught live from a screenshot: clear_rendered_area() used
    to only count explicit '\\n's (len(self.lines) - 1), so a single long
    line that the terminal soft-wraps across several physical rows was
    always treated as 1 row - the cursor never moved up far enough to
    overwrite it, and every keystroke past the wrap point printed a whole
    new duplicate prompt below the last one (dozens of stacked '> ...'
    lines in the screenshot). No pty can observe soft-wrap directly (a bare
    pty doesn't render/wrap - a real terminal emulator does, and no
    terminal-emulation library is available without adding a dependency),
    so this tests the row-counting arithmetic directly instead."""
    import agent_peer.rawline as rawline_mod

    monkeypatch.setattr(rawline_mod.shutil, "get_terminal_size", lambda fallback=(80, 24): os.terminal_size((80, 24)))
    e = LineEditor("> ")

    e.lines = ["x" * 30]
    assert e._visual_rows() == 1

    e.lines = ["x" * 78]  # + 2-char prompt "> " = exactly 80 -> still 1 row
    assert e._visual_rows() == 1

    e.lines = ["x" * 79]  # + prompt = 81 -> wraps to a 2nd row
    assert e._visual_rows() == 2

    e.lines = ["x" * 180]  # + prompt = 182 -> ceil(182/80) = 3 rows
    assert e._visual_rows() == 3

    e.lines = ["first line", "x" * 100]  # continuation line also wraps on its own
    assert e._visual_rows() == 1 + 2  # "... " (4) + 100 = 104 -> ceil(104/80) = 2


def test_reset_clears_cursor_position_not_just_the_text():
    """A stale cursor_col from a previous (longer) message must not leave a
    shorter next one inserting/deleting out of bounds."""
    e = LineEditor("> ")
    e.append_char("hello world")
    e.move_left()
    e.move_left()
    assert e.cursor_col == len("hello world") - 2
    e.reset()
    assert e.cursor_col == 0
    assert e.lines == [""]


pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX-only; rawline's Windows path uses msvcrt instead")


def test_plain_type_and_enter_submits():
    assert "RESULT:'hello world'" in _run_pty([b"hello world", b"\r"])


def test_backspace_removes_the_last_character():
    assert "RESULT:'hello'" in _run_pty([b"helloX", b"\x7f", b"\r"])


def test_esc_clears_the_in_progress_line():
    assert "RESULT:'clean'" in _run_pty([b"garbage text", b"\x1b", b"clean", b"\r"])


def test_arrow_keys_are_ignored_not_a_clear():
    """Real bug, caught live: pressing Up/Left/Down/Right mid-composition
    used to fall through to editor.reset() and wipe everything typed.
    Left/Right now move the cursor (see the tests below) rather than being
    fully ignored, but moving the cursor and landing back at the end before
    Enter must still never touch the text itself. Up/Down remain
    unrecognized/swallowed - there's no history to navigate here."""
    arrows = [b"\x1b[A", b"\x1b[B", b"\x1b[C", b"\x1b[D", b"\x1b[C"]
    assert "RESULT:'keep me'" in _run_pty([b"keep me"] + arrows + [b"\r"])


def test_left_arrow_then_typing_inserts_mid_line_not_at_the_end():
    """The actual fix: Left/Right used to be pure no-ops (see the arrow test
    above, pre-fix), so typing after pressing Left always landed at the end
    of the line instead of where the cursor visually was."""
    assert "RESULT:'helXXlo'" in _run_pty([b"hello", b"\x1b[D\x1b[D", b"XX", b"\r"])


def test_right_arrow_moves_forward_after_moving_left():
    assert "RESULT:'heZllo'" in _run_pty(
        [b"hello", b"\x1b[D" * 5, b"\x1b[C" * 2, b"Z", b"\r"]
    )


def test_backspace_respects_cursor_position_not_always_the_end():
    """Same class of bug as insert: backspace always removed the LAST
    character of the line regardless of where the cursor was."""
    assert "RESULT:'helo'" in _run_pty([b"hello", b"\x1b[D\x1b[D", b"\x7f", b"\r"])


def test_word_left_csi_modifier_jumps_to_previous_word_start():
    """Ctrl+Left/Option+Left send a modified CSI sequence (e.g. \\x1b[1;3D
    for Alt, \\x1b[1;5D for Ctrl) rather than a plain \\x1b[D."""
    assert "RESULT:'hello Xworld'" in _run_pty([b"hello world", b"\x1b[1;3D", b"X", b"\r"])


def test_word_left_meta_b_jumps_to_previous_word_start():
    """The classic readline meta convention (Alt+b) some terminals send
    instead of a CSI-modified arrow."""
    assert "RESULT:'hello Xworld'" in _run_pty([b"hello world", b"\x1bb", b"X", b"\r"])


def test_word_right_jumps_to_next_word_end():
    assert "RESULT:'hello worldX'" in _run_pty(
        [b"hello world", b"\x1b[D" * 5, b"\x1bf", b"X", b"\r"]
    )




def test_trailing_backslash_continues_composing_a_second_line():
    assert "RESULT:'line one\\nline two'" in _run_pty([b"line one\\", b"\r", b"line two", b"\r"])


def test_alt_enter_continues_composing_not_a_clear():
    """The real bug this locks in: ICRNL can translate the \\r half of
    \\x1b\\r into \\n before it's read, and the escape-sequence detector
    must still recognize it as Alt+Enter, not fall through to a plain Esc."""
    assert "RESULT:'first\\nsecond'" in _run_pty([b"first", b"\x1b\r", b"second", b"\r"])


def test_csi_u_shift_enter_continues_composing():
    assert "RESULT:'alpha\\nbeta'" in _run_pty([b"alpha", b"\x1b[13;2u", b"beta", b"\r"])


def test_esc_clears_a_whole_multi_line_composition():
    assert "RESULT:'restart'" in _run_pty([b"first", b"\x1b\r", b"second", b"\x1b", b"restart", b"\r"])


def test_ctrl_c_returns_none_instead_of_crashing():
    """The real bug this locks in: ISIG left enabled meant the kernel
    intercepted Ctrl+C as a real SIGINT before byte 0x03 ever reached our
    own explicit handling for it, crashing the process instead of a clean
    'leave the session' (None)."""
    assert "RESULT:None" in _run_pty([b"partial", b"\x03"])


def test_ctrl_d_on_an_empty_buffer_returns_none():
    assert "RESULT:None" in _run_pty([b"\x04"])


def test_bracketed_paste_becomes_one_multi_line_message():
    """A terminal that supports bracketed paste wraps pasted text in
    \\x1b[200~..\\x1b[201~ - the whole block must land as one composed
    message (still submitted by an explicit Enter), not one submit per
    embedded newline (which would spam the thread with N messages)."""
    paste = b"\x1b[200~" + b"line A\nline B\nline C" + b"\x1b[201~"
    assert "RESULT:'line A\\nline B\\nline C'" in _run_pty([paste, b"\r"])


def test_paste_joins_onto_already_typed_text():
    paste = b"\x1b[200~" + b"pasted" + b"\x1b[201~"
    assert "RESULT:'prefix-pasted'" in _run_pty([b"prefix-", paste, b"\r"])


def test_paste_normalizes_crlf_line_endings():
    """A Windows-clipboard-sourced paste can carry \\r\\n - a raw \\r left in
    the buffer would move the cursor mid-render and visually corrupt the
    line. Also locks in that ICRNL is disabled during raw mode: with it
    left on, the kernel silently rewrites \\r to \\n before this code ever
    sees it, turning \\r\\n into \\n\\n (an extra blank line) - normalizing
    in paste_extend() alone cannot fix bytes that are already wrong by the
    time they arrive."""
    paste = b"\x1b[200~" + b"line A\r\nline B" + b"\x1b[201~"
    assert "RESULT:'line A\\nline B'" in _run_pty([paste, b"\r"])


def test_join_prints_a_permanent_card_for_the_message_you_just_sent():
    """Real bug, caught live by three independent reports (the owner, `agy`,
    `muse`) during hand-walk testing: raw mode's own render() clears the
    typed line to redraw the prompt - unlike canonical input(), which the
    terminal echoes on its own, nothing here ever printed the sent message
    as a permanent line, so it visually vanished the instant Enter was
    pressed. join.py must explicitly print the sent record's own card."""
    import shutil
    import tempfile

    master, slave = pty.openpty()
    home = tempfile.mkdtemp()
    child_script = f"""
import os, sys
os.environ["HOME"] = {home!r}
sys.path.insert(0, {REPO_ROOT!r})
from agent_peer.join import run_join
sys.stderr.write("CHILD READY\\n"); sys.stderr.flush()
run_join("cardtest", "foreman")
"""
    pid = os.fork()
    if pid == 0:
        os.close(master)
        os.setsid()
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        os.close(slave)
        os.execvp(sys.executable, [sys.executable, "-c", child_script])
    else:
        os.close(slave)
        try:
            buf = b""
            deadline = time.time() + 6
            while b"CHILD READY" not in buf and time.time() < deadline:
                if select.select([master], [], [], 0.2)[0]:
                    buf += os.read(master, 4096)
            time.sleep(0.5)

            os.write(master, b"hello this should stay visible")
            time.sleep(0.1)
            os.write(master, b"\r")
            time.sleep(0.7)
            os.write(master, b"\x04")  # Ctrl+D to leave
            time.sleep(0.7)

            out = b""
            try:
                while select.select([master], [], [], 0.4)[0]:
                    chunk = os.read(master, 4096)
                    if not chunk:
                        break
                    out += chunk
            except OSError:
                pass
            for _ in range(30):
                wpid, _status = os.waitpid(pid, os.WNOHANG)
                if wpid != 0:
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            text = out.decode(errors="replace")
            assert "hello this should stay visible" in text, text
        finally:
            os.close(master)
            shutil.rmtree(home, ignore_errors=True)
