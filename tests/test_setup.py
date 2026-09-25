"""One-door install/setup (docs/tasks/0022): detection, skill copy, update check.

Pure modules are imported directly (they bind no paths at import time);
CLI end-to-end runs go through the subprocess helper with an isolated $HOME.
"""

import json
import os
import stat
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_peer import harness_detect, setup_tui, update_check
from tests.helpers import isolated_home, run_cli

FAKE_BINARIES = ("agy", "codex", "muse", "pi", "opencode", "claude")


class DetectionTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()
        self._saved_path = os.environ.get("PATH", "")
        self.bindir = os.path.join(self.home, "bin")
        os.makedirs(self.bindir)
        os.environ["PATH"] = self.bindir

    def tearDown(self):
        os.environ["PATH"] = self._saved_path
        self._home_cm.__exit__(None, None, None)

    def _drop_binary(self, name):
        path = os.path.join(self.bindir, name)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def test_empty_home_detects_nothing(self):
        for entry in harness_detect.detect_all(self.home):
            self.assertFalse(entry["installed"], entry["id"])
            self.assertFalse(entry["skill_present"], entry["id"])

    def test_binary_and_dir_signals(self):
        self._drop_binary("agy")
        os.makedirs(os.path.join(self.home, ".codex"))
        by_id = {e["id"]: e for e in harness_detect.detect_all(self.home)}
        self.assertTrue(by_id["agy"]["installed"])
        self.assertIn("bin/agy", by_id["agy"]["evidence"])
        self.assertTrue(by_id["codex"]["installed"])
        self.assertIn(".codex", by_id["codex"]["evidence"])
        self.assertFalse(by_id["muse"]["installed"])

    def test_pi_matches_omp_binary(self):
        self._drop_binary("omp")
        by_id = {e["id"]: e for e in harness_detect.detect_all(self.home)}
        self.assertTrue(by_id["pi"]["installed"])

    def test_all_sources_resolve(self):
        for harness in harness_detect.HARNESSES:
            source = harness_detect.skill_source_path(harness["id"])
            self.assertTrue(source and os.path.isfile(source), harness["id"])

    def test_unknown_harness_raises(self):
        with self.assertRaises(KeyError):
            harness_detect.skill_source_path("nope")
        with self.assertRaises(KeyError):
            harness_detect.target_path("nope")


class SkillCopyTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()

    def tearDown(self):
        self._home_cm.__exit__(None, None, None)

    def test_install_copies_exact_content(self):
        result = setup_tui.install_skills(["muse", "pi"], self.home)
        self.assertEqual(result, {"installed": ["muse", "pi"], "skipped": []})
        for hid in ("muse", "pi"):
            target = harness_detect.target_path(hid, self.home)
            with open(target, encoding="utf-8") as fh:
                installed = fh.read()
            with open(harness_detect.skill_source_path(hid), encoding="utf-8") as fh:
                self.assertEqual(installed, fh.read())

    def test_install_marks_missing_source(self):
        real = harness_detect.skill_source_path
        harness_detect.skill_source_path = lambda hid: None
        try:
            result = setup_tui.install_skills(["muse"], self.home)
        finally:
            harness_detect.skill_source_path = real
        self.assertEqual(result, {"installed": [], "skipped": ["muse"]})

    def test_remove_deletes_and_reports_missing(self):
        setup_tui.install_skills(["opencode"], self.home)
        result = setup_tui.remove_skills(["opencode", "agy"], self.home)
        self.assertEqual(result, {"removed": ["opencode"], "missing": ["agy"]})
        self.assertFalse(os.path.exists(harness_detect.target_path("opencode", self.home)))

    def test_initial_checked_prefers_current_state(self):
        setup_tui.install_skills(["codex"], self.home)
        entries = harness_detect.detect_all(self.home)
        checked = setup_tui._initial_checked(entries)
        self.assertIn("codex", checked)


class UpdateCheckTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()

    def tearDown(self):
        self._home_cm.__exit__(None, None, None)

    def test_version_compare(self):
        self.assertTrue(update_check.is_newer("0.4.4", "0.4.3"))
        self.assertTrue(update_check.is_newer("0.10.0", "0.9.9"))
        self.assertFalse(update_check.is_newer("0.4.3", "0.4.3"))
        self.assertFalse(update_check.is_newer("0.4.2", "0.4.3"))
        self.assertFalse(update_check.is_newer("bogus", "0.4.3"))

    def test_fresh_cache_needs_no_network(self):
        path = update_check.cache_path(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"last_checked": time.time(), "latest_version": "9.9.9"}, fh)

        def _boom(timeout=0):
            raise AssertionError("network must not be hit with a fresh cache")

        real = update_check._fetch_latest
        update_check._fetch_latest = _boom
        try:
            self.assertEqual(update_check.get_latest_version(self.home), "9.9.9")
        finally:
            update_check._fetch_latest = real

    def test_stale_cache_fetches_and_rewrites(self):
        path = update_check.cache_path(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"last_checked": 1, "latest_version": "0.1.0"}, fh)
        real = update_check._fetch_latest
        update_check._fetch_latest = lambda timeout=0: "0.4.4"
        try:
            self.assertEqual(update_check.get_latest_version(self.home), "0.4.4")
        finally:
            update_check._fetch_latest = real
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["latest_version"], "0.4.4")

    def test_offline_falls_back_to_cache_then_none(self):
        real = update_check._fetch_latest
        update_check._fetch_latest = lambda timeout=0: (_ for _ in ()).throw(OSError("down"))
        try:
            self.assertIsNone(update_check.get_latest_version(self.home))
            path = update_check.cache_path(self.home)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"last_checked": 1, "latest_version": "0.4.2"}, fh)
            self.assertEqual(update_check.get_latest_version(self.home), "0.4.2")
        finally:
            update_check._fetch_latest = real


class SetupCliTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()
        self._saved_path = os.environ.get("PATH", "")
        self.bindir = os.path.join(self.home, "bin")
        os.makedirs(self.bindir)
        for name in FAKE_BINARIES:
            path = os.path.join(self.bindir, name)
            with open(path, "w") as fh:
                fh.write("#!/bin/sh\n")
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        os.environ["PATH"] = self.bindir

    def tearDown(self):
        os.environ["PATH"] = self._saved_path
        self._home_cm.__exit__(None, None, None)

    def test_list_shows_detection(self):
        proc = run_cli(["setup", "--list"], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for name in FAKE_BINARIES:
            self.assertIn(name, proc.stdout)

    def test_all_installs_detected_skills(self):
        proc = run_cli(["setup", "--all"], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for hid in ("agy", "codex", "muse", "pi", "opencode", "claude"):
            self.assertTrue(os.path.isfile(harness_detect.target_path(hid, self.home)), hid)

    def test_harness_flag_and_remove_roundtrip(self):
        proc = run_cli(["setup", "--harness", "muse"], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(harness_detect.target_path("muse", self.home)))
        proc = run_cli(["setup", "--harness", "muse", "--remove"], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(os.path.exists(harness_detect.target_path("muse", self.home)))

    def test_unknown_harness_exits_2(self):
        proc = run_cli(["setup", "--harness", "nope"], self.home)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown harness", proc.stderr)

    def test_bare_setup_without_tty_fails_clearly(self):
        proc = run_cli(["setup"], self.home)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--all", proc.stderr)

    def test_setup_cancellation_prints_cancelled_and_no_changes(self):
        from agent_peer import cli
        from unittest.mock import patch
        import argparse
        import io

        fake_out = io.StringIO()
        with patch.object(setup_tui, "pick_harnesses", return_value=(None, False)):
            with patch("sys.stdout", fake_out):
                parser = argparse.ArgumentParser()
                parser.add_argument("--all", action="store_true")
                parser.add_argument("--harness", action="append", default=[])
                parser.add_argument("--rules", action="store_true")
                parser.add_argument("--no-rules", action="store_true")
                parser.add_argument("--remove", action="store_true")
                parser.add_argument("--list", action="store_true")
                args = parser.parse_args([])
                with patch("os.environ", dict(os.environ, HOME=self.home)):
                    cli.cmd_setup(args)

        self.assertIn("setup cancelled - no changes made", fake_out.getvalue())
        # Verify no skill or rules files installed
        for hid in ("agy", "codex", "muse", "pi", "opencode", "claude"):
            self.assertFalse(os.path.isfile(harness_detect.target_path(hid, self.home)))
            self.assertFalse(os.path.isfile(harness_detect.rules_target_path(hid, self.home)))

    def test_pick_harnesses_keyboard_interrupt_returns_none_false(self):
        from unittest.mock import patch
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True):
            with patch("curses.wrapper", side_effect=KeyboardInterrupt):
                entries = harness_detect.detect_all(self.home)
                selected, rules = setup_tui.pick_harnesses(entries)
                self.assertIsNone(selected)
                self.assertFalse(rules)


if __name__ == "__main__":
    unittest.main()
