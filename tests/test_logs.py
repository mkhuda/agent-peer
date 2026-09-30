"""Direct unit tests for logs.py's thread-view formatting (colored
per-harness badges, chat-style grouping) - fast, no subprocess needed since
these are pure string-formatting functions."""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_peer.logs import format_thread_entry, format_harness_fill_badge, resolve_agent_type

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _bg_code(badge_text: str) -> str:
    """Pulls out the '48;5;N' background color code from a rendered badge."""
    m = re.search(r"48;5;(\d+)", badge_text)
    return m.group(1) if m else None


def _fg_code(badge_text: str) -> str:
    m = re.search(r"38;5;(\d+)", badge_text)
    return m.group(1) if m else None


class HarnessBadgeTest(unittest.TestCase):
    def test_you_badge_text_contrasts_with_its_own_background(self):
        # Regression: the YOU badge's background and text color were both
        # 256-color 255 (bright white) - text invisible against its own fill.
        badge = format_harness_fill_badge("Claude", use_color=True, is_you=True)
        self.assertNotEqual(_bg_code(badge), _fg_code(badge))
        self.assertIn("YOU", _strip_ansi(badge))

    def test_each_known_harness_gets_a_distinct_background(self):
        types = ["CLAUDE", "CODEX", "AGY", "MUSE", "PI", "OPENCODE", "TELEGRAM"]
        bgs = [_bg_code(format_harness_fill_badge(t, use_color=True)) for t in types]
        self.assertEqual(len(bgs), len(set(bgs)), f"expected distinct backgrounds, got {bgs}")

    def test_badge_text_always_contrasts_with_its_background(self):
        for t in ["CLAUDE", "CODEX", "AGY", "MUSE", "PI", "OPENCODE", "TELEGRAM"]:
            badge = format_harness_fill_badge(t, use_color=True)
            self.assertNotEqual(_bg_code(badge), _fg_code(badge), f"{t} badge text unreadable")

    def test_no_color_mode_has_no_escape_codes(self):
        badge = format_harness_fill_badge("CODEX", use_color=False)
        self.assertEqual(badge, "[CODEX]")


class ResolveAgentTypeTest(unittest.TestCase):
    def test_recognizes_every_known_harness_name_pattern(self):
        cases = {
            "codex-8763": "CODEX", "muse-50125": "MUSE", "agy-86748": "AGY",
            "opencode-1": "OPENCODE", "pi-9": "PI", "omp-3": "PI",
        }
        for name, expected in cases.items():
            self.assertEqual(resolve_agent_type(name, {}), expected, name)

    def test_unrecognized_unregistered_name_gets_no_badge(self):
        self.assertEqual(resolve_agent_type("rg", {}), "")
        self.assertEqual(resolve_agent_type("foreman", {}), "")

    def test_telegram_bridge_has_its_own_badge_and_color(self):
        self.assertEqual(resolve_agent_type("foreman-telegram", {}), "TELEGRAM")
        types = ["CLAUDE", "CODEX", "AGY", "MUSE", "PI", "OPENCODE", "TELEGRAM"]
        bgs = [_bg_code(format_harness_fill_badge(t, use_color=True)) for t in types]
        self.assertEqual(len(bgs), len(set(bgs)))

    def test_registered_native_claude_with_custom_name_is_claude(self):
        from agent_peer import logs

        orig = logs.get_active_sessions
        logs.get_active_sessions = lambda: [
            {"pid": 7, "name": "the-cto"}, {"pid": 8, "name": "super-ui", "agentType": "CODEX"},
        ]
        try:
            cache = logs.get_session_cache()
        finally:
            logs.get_active_sessions = orig
        self.assertEqual(resolve_agent_type("the-cto", cache), "Claude")
        self.assertEqual(resolve_agent_type("super-ui", cache), "CODEX")

    def test_plain_header_omits_empty_badge(self):
        out = format_thread_entry({"seq": 1, "ts": 0, "from": "rg", "content": "hi"}, use_color=False, session_cache={})
        self.assertNotIn("[]", out)
        self.assertIn("rg", out)

    def test_cache_entry_wins_over_name_pattern_guessing(self):
        cache = {"weird-codex-named-claude-session": {"type": "Claude"}}
        self.assertEqual(
            resolve_agent_type("weird-codex-named-claude-session", cache), "Claude"
        )


class FormatThreadEntryGroupingTest(unittest.TestCase):
    def _record(self, seq, sender, content):
        return {"seq": seq, "from": sender, "content": content, "ts": 0}

    def test_same_sender_as_last_sender_omits_the_header(self):
        r = self._record(2, "alice", "second message")
        text = format_thread_entry(r, use_color=False, session_cache={}, last_sender="alice")
        self.assertNotIn("alice", text)
        self.assertIn("second message", text)

    def test_different_sender_than_last_sender_shows_the_header(self):
        r = self._record(2, "bob", "hi")
        text = format_thread_entry(r, use_color=False, session_cache={}, last_sender="alice")
        self.assertIn("bob", text)

    def test_first_message_with_no_last_sender_shows_the_header(self):
        r = self._record(1, "alice", "hi")
        text = format_thread_entry(r, use_color=False, session_cache={}, last_sender=None)
        self.assertIn("alice", text)


class StaleSessionCacheTest(unittest.TestCase):
    """A custom-named session (no harness word in its name) that registers
    after a live view built its cache must not be mislabeled as Claude."""

    def setUp(self):
        from agent_peer import logs

        self.logs = logs
        self._orig_get_cache = logs.get_session_cache
        logs._last_cache_refresh[0] = 0.0

    def tearDown(self):
        self.logs.get_session_cache = self._orig_get_cache

    def _record(self):
        return {"seq": 1, "ts": 0, "from": "super-ui", "content": "halo"}

    def test_unknown_custom_name_is_refreshed_from_live_registry(self):
        self.logs.get_session_cache = lambda: {
            "super-ui": {"name": "super-ui", "type": "CODEX", "pid": "1"}
        }
        stale = {}
        out = _strip_ansi(format_thread_entry(self._record(), use_color=False, session_cache=stale))
        self.assertIn("[CODEX] super-ui", out)
        self.assertIn("super-ui", stale)  # refreshed in place, later entries need no rescan

    def test_refresh_is_throttled_so_dead_senders_dont_rescan_per_message(self):
        calls = []

        def counting():
            calls.append(1)
            return {}

        self.logs.get_session_cache = counting
        cache = {}
        for _ in range(5):
            format_thread_entry(self._record(), use_color=False, session_cache=cache)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
