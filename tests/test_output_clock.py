"""thread/wait output lines carry the message's time so a reader can judge its age."""

import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_peer.cli import _clock


class ClockTest(unittest.TestCase):
    def test_recent_is_time_only(self):
        self.assertRegex(_clock(time.time() - 5), r"^ · \d\d:\d\d:\d\d$")

    def test_older_than_a_minute_shows_ago(self):
        self.assertRegex(_clock(time.time() - 42 * 60), r"^ · \d\d:\d\d:\d\d \(42m ago\)$")
        self.assertRegex(_clock(time.time() - 3 * 3600), r"\(3h ago\)$")

    def test_older_than_a_day_adds_date(self):
        self.assertRegex(_clock(time.time() - 3 * 86400), r"^ · \d\d-\d\d \d\d:\d\d:\d\d \(72h ago\)$")

    def test_missing_time_is_empty(self):
        self.assertEqual(_clock(None), "")
        self.assertEqual(_clock(0), "")
        self.assertEqual(_clock("x"), "")


if __name__ == "__main__":
    unittest.main()
