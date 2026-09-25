"""Tests for Task 0036: Global policy injection in setup (Phase 1).

Tests marker block engine, rules installation/removal across harnesses,
and CLI dispatch (--rules, --no-rules).
"""

import os
import shutil
import stat
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_peer import harness_detect, setup_rules
from tests.helpers import isolated_home, run_cli


class RulesTargetPathTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()

    def tearDown(self):
        self._home_cm.__exit__(None, None, None)

    def test_rules_target_paths_resolve(self):
        expected = {
            "agy": os.path.join(self.home, ".gemini", "GEMINI.md"),
            "codex": os.path.join(self.home, ".codex", "AGENTS.md"),
            "muse": os.path.join(self.home, ".agents", "AGENTS.md"),
            "pi": os.path.join(self.home, ".pi", "agent", "AGENTS.md"),
            "opencode": os.path.join(self.home, ".config", "opencode", "AGENTS.md"),
            "claude": os.path.join(self.home, ".claude", "CLAUDE.md"),
        }
        for hid, exp_path in expected.items():
            self.assertEqual(harness_detect.rules_target_path(hid, self.home), exp_path)

    def test_unknown_harness_raises(self):
        with self.assertRaises(KeyError):
            harness_detect.rules_target_path("unknown_harness", self.home)


class MarkerBlockEngineTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()
        self.target_file = os.path.join(self.home, "test_rules.md")

    def tearDown(self):
        self._home_cm.__exit__(None, None, None)

    def test_inject_creates_new_file(self):
        content = "## Mesh Rules\n- Be nice\n"
        injected = setup_rules.inject_marker_block(self.target_file, content)
        self.assertTrue(injected)
        self.assertTrue(os.path.isfile(self.target_file))
        with open(self.target_file, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("# >>> agent-peer mesh discipline >>>", text)
        self.assertIn("## Mesh Rules\n- Be nice", text)
        self.assertIn("# <<< agent-peer mesh discipline <<<", text)

    def test_inject_preserves_existing_user_content(self):
        user_header = "# My Custom Instructions\nDo not delete this line.\n"
        with open(self.target_file, "w", encoding="utf-8") as fh:
            fh.write(user_header)

        setup_rules.inject_marker_block(self.target_file, "## Mesh Rules\n")

        with open(self.target_file, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith(user_header))
        self.assertIn("# >>> agent-peer mesh discipline >>>", text)

    def test_inject_is_idempotent(self):
        # Running injection twice replaces between markers with no duplicate markers
        setup_rules.inject_marker_block(self.target_file, "v1 rules")
        setup_rules.inject_marker_block(self.target_file, "v2 updated rules")

        with open(self.target_file, encoding="utf-8") as fh:
            text = fh.read()

        self.assertEqual(text.count("# >>> agent-peer mesh discipline >>>"), 1)
        self.assertEqual(text.count("# <<< agent-peer mesh discipline <<<"), 1)
        self.assertIn("v2 updated rules", text)
        self.assertNotIn("v1 rules", text)

    def test_remove_marker_block_leaves_user_content_intact(self):
        before = "# User rules top\n"
        after = "\n# User rules bottom\n"
        setup_rules.inject_marker_block(self.target_file, "injected rules")
        with open(self.target_file, encoding="utf-8") as fh:
            middle = fh.read()

        # Wrap with user content before and after
        with open(self.target_file, "w", encoding="utf-8") as fh:
            fh.write(before + middle + after)

        removed = setup_rules.remove_marker_block(self.target_file)
        self.assertTrue(removed)

        with open(self.target_file, encoding="utf-8") as fh:
            result = fh.read()

        self.assertNotIn("agent-peer mesh discipline", result)
        self.assertIn("# User rules top", result)
        self.assertIn("# User rules bottom", result)

    def test_remove_nonexistent_marker_returns_false(self):
        with open(self.target_file, "w", encoding="utf-8") as fh:
            fh.write("# Pure user content\n")
        removed = setup_rules.remove_marker_block(self.target_file)
        self.assertFalse(removed)
        with open(self.target_file, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "# Pure user content\n")


    def test_remove_marker_block_exact_spacing(self):
        start_marker = "# >>> agent-peer mesh discipline >>>"
        end_marker = "# <<< agent-peer mesh discipline <<<"
        block = f"{start_marker}\ncontent\n{end_marker}"
        content = f"# top\n\n{block}\n\n# bottom\n"
        with open(self.target_file, "w", encoding="utf-8") as fh:
            fh.write(content)
        removed = setup_rules.remove_marker_block(self.target_file)
        self.assertTrue(removed)
        with open(self.target_file, encoding="utf-8") as fh:
            result = fh.read()
        self.assertEqual(result, "# top\n\n# bottom\n")

    def test_orphan_start_marker_does_not_consume_user_text(self):
        start_marker = "# >>> agent-peer mesh discipline >>>"
        content = f"# header\n{start_marker}\n# user note 1\n# user note 2\n"
        with open(self.target_file, "w", encoding="utf-8") as fh:
            fh.write(content)
        removed = setup_rules.remove_marker_block(self.target_file)
        self.assertTrue(removed)
        with open(self.target_file, encoding="utf-8") as fh:
            result = fh.read()
        self.assertNotIn(start_marker, result)
        self.assertEqual(result, "# header\n# user note 1\n# user note 2\n")

    def test_orphan_end_marker_does_not_consume_user_text(self):
        end_marker = "# <<< agent-peer mesh discipline <<<"
        content = f"# header\n# user note 1\n{end_marker}\n# user note 2\n"
        with open(self.target_file, "w", encoding="utf-8") as fh:
            fh.write(content)
        removed = setup_rules.remove_marker_block(self.target_file)
        self.assertTrue(removed)
        with open(self.target_file, encoding="utf-8") as fh:
            result = fh.read()
        self.assertNotIn(end_marker, result)
        self.assertEqual(result, "# header\n# user note 1\n# user note 2\n")

    def test_inject_recovers_from_orphan_start_marker_without_losing_user_notes(self):
        start_marker = "# >>> agent-peer mesh discipline >>>"
        content = f"# header\n{start_marker}\n# user note 1\n"
        with open(self.target_file, "w", encoding="utf-8") as fh:
            fh.write(content)
        setup_rules.inject_marker_block(self.target_file, "new rules")
        with open(self.target_file, encoding="utf-8") as fh:
            result = fh.read()
        self.assertIn("new rules", result)
        self.assertIn("# user note 1\n", result)
        # And now remove_marker_block must not delete user note 1
        setup_rules.remove_marker_block(self.target_file)
        with open(self.target_file, encoding="utf-8") as fh:
            result_after_remove = fh.read()
        self.assertIn("# user note 1\n", result_after_remove)

    def test_reversed_markers_rejected_by_has_marker_block_and_cleaned(self):
        start_marker = "# >>> agent-peer mesh discipline >>>"
        end_marker = "# <<< agent-peer mesh discipline <<<"
        reversed_content = f"{end_marker}\n# Middle text\n{start_marker}\n"
        with open(self.target_file, "w", encoding="utf-8") as fh:
            fh.write(reversed_content)

        # has_marker_block must return False for reversed markers
        self.assertFalse(setup_rules.has_marker_block(self.target_file))

        # remove_marker_block removes reversed markers and returns True
        removed = setup_rules.remove_marker_block(self.target_file)
        self.assertTrue(removed)
        with open(self.target_file, encoding="utf-8") as fh:
            cleaned = fh.read()
        self.assertNotIn(start_marker, cleaned)
        self.assertNotIn(end_marker, cleaned)
        self.assertEqual(cleaned, "# Middle text\n")

    def test_inline_orphan_markers_preserve_surrounding_line_text(self):
        start_marker = "# >>> agent-peer mesh discipline >>>"
        end_marker = "# <<< agent-peer mesh discipline <<<"
        inline_content = f"User prefix {start_marker} user suffix\nAnother line {end_marker} with text\n"
        with open(self.target_file, "w", encoding="utf-8") as fh:
            fh.write(inline_content)

        # remove_marker_block removes inline marker sentinels while preserving user text
        removed = setup_rules.remove_marker_block(self.target_file)
        self.assertTrue(removed)
        with open(self.target_file, encoding="utf-8") as fh:
            cleaned = fh.read()
        self.assertNotIn(start_marker, cleaned)
        self.assertNotIn(end_marker, cleaned)
        self.assertIn("User prefix", cleaned)
        self.assertIn("user suffix", cleaned)
        self.assertIn("Another line", cleaned)
        self.assertIn("with text", cleaned)


class RulesPresentDetectionTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()

    def tearDown(self):
        self._home_cm.__exit__(None, None, None)

    def test_rules_present_detects_marker_not_just_file(self):
        codex_rules = harness_detect.rules_target_path("codex", self.home)
        os.makedirs(os.path.dirname(codex_rules), exist_ok=True)
        with open(codex_rules, "w", encoding="utf-8") as fh:
            fh.write("# User custom AGENTS.md\nNo mesh rules here.\n")

        entries = harness_detect.detect_all(self.home)
        codex_entry = next(e for e in entries if e["id"] == "codex")
        self.assertFalse(codex_entry["rules_present"])

        setup_rules.inject_marker_block(codex_rules, "some mesh rules")
        entries = harness_detect.detect_all(self.home)
        codex_entry = next(e for e in entries if e["id"] == "codex")
        self.assertTrue(codex_entry["rules_present"])


class InstallRemoveRulesTest(unittest.TestCase):
    def setUp(self):
        self._home_cm = isolated_home()
        self.home = self._home_cm.__enter__()

    def tearDown(self):
        self._home_cm.__exit__(None, None, None)

    def test_install_and_remove_rules_per_harness(self):
        harnesses = ["agy", "codex", "claude"]
        res = setup_rules.install_rules(harnesses, self.home)
        self.assertEqual(sorted(res["installed"]), sorted(harnesses))

        for hid in harnesses:
            path = harness_detect.rules_target_path(hid, self.home)
            self.assertTrue(os.path.isfile(path), f"Missing rules file for {hid}")
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("IPC Mesh Discipline (agent-peer)", content)

        # Remove rules
        rem_res = setup_rules.remove_rules(harnesses, self.home)
        self.assertEqual(sorted(rem_res["removed"]), sorted(harnesses))
        for hid in harnesses:
            path = harness_detect.rules_target_path(hid, self.home)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    content = fh.read()
                self.assertNotIn("agent-peer mesh discipline", content)


class CliSetupRulesTest(unittest.TestCase):
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

    def test_cli_setup_default_does_not_install_rules(self):
        self._drop_binary("codex")
        res = run_cli(["setup", "--all"], home=self.home)
        self.assertEqual(res.returncode, 0)
        # Skills should be installed
        codex_skill = harness_detect.target_path("codex", self.home)
        self.assertTrue(os.path.isfile(codex_skill))
        # Rules should NOT be installed
        codex_rules = harness_detect.rules_target_path("codex", self.home)
        self.assertFalse(os.path.isfile(codex_rules))

    def test_cli_setup_with_rules_flag_installs_both(self):
        self._drop_binary("codex")
        res = run_cli(["setup", "--all", "--rules"], home=self.home)
        self.assertEqual(res.returncode, 0)
        codex_skill = harness_detect.target_path("codex", self.home)
        codex_rules = harness_detect.rules_target_path("codex", self.home)
        self.assertTrue(os.path.isfile(codex_skill))
        self.assertTrue(os.path.isfile(codex_rules))
        with open(codex_rules, encoding="utf-8") as fh:
            self.assertIn("agent-peer mesh discipline", fh.read())

    def test_cli_setup_no_rules_removes_rules_preserves_skills(self):
        self._drop_binary("codex")
        run_cli(["setup", "--all", "--rules"], home=self.home)
        codex_skill = harness_detect.target_path("codex", self.home)
        codex_rules = harness_detect.rules_target_path("codex", self.home)
        self.assertTrue(os.path.isfile(codex_skill))
        self.assertTrue(os.path.isfile(codex_rules))

        res = run_cli(["setup", "--all", "--no-rules"], home=self.home)
        self.assertEqual(res.returncode, 0)
        # Skill still intact
        self.assertTrue(os.path.isfile(codex_skill))
        # Rules marker removed
        with open(codex_rules, encoding="utf-8") as fh:
            self.assertNotIn("agent-peer mesh discipline", fh.read())

    def test_cli_setup_rules_and_no_rules_mutually_exclusive(self):
        self._drop_binary("codex")
        res = run_cli(["setup", "--all", "--rules", "--no-rules"], home=self.home)
        self.assertEqual(res.returncode, 2)
        self.assertIn("not allowed with argument", res.stderr)

    def test_cli_setup_standalone_no_rules_non_interactive(self):
        self._drop_binary("codex")
        run_cli(["setup", "--all", "--rules"], home=self.home)
        codex_rules = harness_detect.rules_target_path("codex", self.home)
        self.assertTrue(os.path.isfile(codex_rules))
        with open(codex_rules, encoding="utf-8") as fh:
            self.assertIn("agent-peer mesh discipline", fh.read())

        res = run_cli(["setup", "--no-rules"], home=self.home)
        self.assertEqual(res.returncode, 0)
        self.assertIn("removed rules for codex", res.stdout)
        with open(codex_rules, encoding="utf-8") as fh:
            self.assertNotIn("agent-peer mesh discipline", fh.read())

    def test_cli_setup_remove_rules_cleans_rules_present_independently(self):
        self._drop_binary("codex")
        codex_rules = harness_detect.rules_target_path("codex", self.home)
        setup_rules.inject_marker_block(codex_rules, "mesh rules")
        codex_skill = harness_detect.target_path("codex", self.home)
        self.assertFalse(os.path.isfile(codex_skill))
        self.assertTrue(os.path.isfile(codex_rules))

        res = run_cli(["setup", "--all", "--remove", "--rules"], home=self.home)
        self.assertEqual(res.returncode, 0)
        self.assertIn("removed rules for codex", res.stdout)
        with open(codex_rules, encoding="utf-8") as fh:
            self.assertNotIn("agent-peer mesh discipline", fh.read())

    def test_cli_setup_rules_on_already_installed_skill(self):
        self._drop_binary("codex")
        res = run_cli(["setup", "--all"], home=self.home)
        self.assertEqual(res.returncode, 0)
        codex_skill = harness_detect.target_path("codex", self.home)
        codex_rules = harness_detect.rules_target_path("codex", self.home)
        self.assertTrue(os.path.isfile(codex_skill))
        self.assertFalse(os.path.isfile(codex_rules))

        res2 = run_cli(["setup", "--harness", "codex", "--rules"], home=self.home)
        self.assertEqual(res2.returncode, 0)
        self.assertTrue(os.path.isfile(codex_rules))
        with open(codex_rules, encoding="utf-8") as fh:
            self.assertIn("agent-peer mesh discipline", fh.read())


if __name__ == "__main__":
    unittest.main()
