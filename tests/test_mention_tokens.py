"""Direct unit tests for thread.py's _mention_tokens() code-span stripping -
fast, no subprocess/socket needed since this is pure parsing logic.
Complements the end-to-end knock-suppression tests in
test_thread_presence_lifecycle.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_peer.thread import _mention_tokens

BT3 = "`" * 3
TILDE3 = "~" * 3


class MentionTokensCodeSpanTest(unittest.TestCase):
    def test_plain_mention_extracted(self):
        self.assertEqual(_mention_tokens("@alice hi"), {"alice"})

    def test_single_backtick_inline_span_stripped(self):
        self.assertEqual(_mention_tokens("see `@alice` for the example"), set())

    def test_triple_backtick_fence_stripped(self):
        self.assertEqual(_mention_tokens(f"{BT3}\n@alice\n{BT3}"), set())

    def test_tilde_fence_stripped(self):
        self.assertEqual(_mention_tokens(f"{TILDE3}\n@alice\n{TILDE3}"), set())

    def test_multiline_inline_span_stripped(self):
        self.assertEqual(_mention_tokens("`code\n@alice\nmore code`"), set())

    def test_longer_backtick_run_delimiter_stripped(self):
        # CommonMark: a code span containing a literal backtick uses a
        # longer delimiter run, e.g. ``code with ` inside``.
        self.assertEqual(_mention_tokens("``@alice has a ` in it``"), set())

    def test_mismatched_fence_delimiters_do_not_pair(self):
        # A backtick-opened fence must not be closed by a tilde fence (or
        # vice versa) - the mention after a mismatched "closer" is real text.
        content = f"{BT3}\nnot actually closed here\n{TILDE3} @alice"
        self.assertEqual(_mention_tokens(content), {"alice"})

    def test_mention_outside_code_span_still_extracted(self):
        self.assertEqual(_mention_tokens("`quoted @bob` but also plain @alice"), {"alice"})

    def test_closer_run_longer_than_opener_still_closes(self):
        # Valid CommonMark for FENCED blocks: a closing fence run may be
        # longer than the opener's (same character). The backreference only
        # needs to find its N-length run as a substring, which a longer run
        # of the same character always contains.
        self.assertEqual(_mention_tokens(f"{BT3}\n@alice\n{BT3}`"), set())
        self.assertEqual(_mention_tokens(f"{BT3}\n@alice\n{BT3}` @bob"), {"bob"})

    def test_odd_backtick_run_inside_single_span_does_not_leak_mention(self):
        # Regression: a stray ``` (three backticks, not meant as a fence)
        # typed inside what the author meant as a single-backtick span used
        # to confuse a naive regex into pairing the wrong backticks,
        # leaving the mention after it unstripped.
        self.assertEqual(_mention_tokens("x `before ``` @alice `"), set())

    def test_inline_span_requires_exact_length_match_not_just_shorter(self):
        # CommonMark inline spans (unlike fences) require the closer to be
        # exactly the same run length as the opener - a run of a different
        # length is just literal backticks, not a delimiter pair.
        self.assertEqual(_mention_tokens("`a````b` @alice"), {"alice"})


if __name__ == "__main__":
    unittest.main()
