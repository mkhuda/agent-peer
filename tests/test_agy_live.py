"""Tests for agy_live.py's model-matching quota selection - regression
coverage for the bug where an active model with no quotaInfo at all got
silently masked by an unrelated, unused sibling model's 100%."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_peer.agy_live import _pick_5h_quota


def _model(provider, remaining_fraction=None, reset_time=None):
    if remaining_fraction is None:
        return {"apiProvider": provider}
    return {
        "apiProvider": provider,
        "quotaInfo": {"remainingFraction": remaining_fraction, "resetTime": reset_time},
    }


GEMINI = "API_PROVIDER_GOOGLE_GEMINI"
ANTHROPIC = "API_PROVIDER_ANTHROPIC_VERTEX"


class PickFiveHourQuotaTest(unittest.TestCase):
    def test_active_model_without_quota_is_not_masked_by_sibling(self):
        # The reported bug: caller is on gemini-3.8-flash-tiered (27.1% real,
        # but Google's fetchAvailableModels doesn't track its quota here),
        # while an unrelated, untouched gemini-2.5-pro sibling sits at 100%.
        data = {
            "models": {
                "gemini-3.8-flash-tiered": _model(GEMINI),  # no quotaInfo at all
                "gemini-2.5-pro": _model(GEMINI, remaining_fraction=1.0),
            }
        }
        result = _pick_5h_quota(data, "Gemini 3.8 Flash (Medium)")
        self.assertIsNone(result["gemini_5h"])

    def test_active_model_with_quota_is_selected_over_sibling(self):
        data = {
            "models": {
                "gemini-3.8-flash-tiered": _model(GEMINI, remaining_fraction=0.271),
                "gemini-2.5-pro": _model(GEMINI, remaining_fraction=1.0),
            }
        }
        result = _pick_5h_quota(data, "Gemini 3.8 Flash (Medium)")
        self.assertEqual(result["gemini_5h"]["remaining_pct"], 27.1)

    def test_no_hint_falls_back_to_most_constrained(self):
        data = {
            "models": {
                "gemini-3.8-flash-tiered": _model(GEMINI, remaining_fraction=0.271),
                "gemini-2.5-pro": _model(GEMINI, remaining_fraction=1.0),
            }
        }
        result = _pick_5h_quota(data, None)
        self.assertEqual(result["gemini_5h"]["remaining_pct"], 27.1)

    def test_hint_not_present_in_response_falls_back(self):
        data = {"models": {"gemini-2.5-pro": _model(GEMINI, remaining_fraction=0.6)}}
        result = _pick_5h_quota(data, "Gemini 3.8 Flash (Medium)")
        self.assertEqual(result["gemini_5h"]["remaining_pct"], 60.0)

    def test_third_party_bucket_unaffected_by_gemini_hint(self):
        data = {
            "models": {
                "gemini-3.8-flash-tiered": _model(GEMINI),
                "claude-sonnet": _model(ANTHROPIC, remaining_fraction=1.0),
            }
        }
        result = _pick_5h_quota(data, "Gemini 3.8 Flash (Medium)")
        self.assertIsNone(result["gemini_5h"])
        self.assertEqual(result["claude_gpt_5h"]["remaining_pct"], 100.0)

    def test_no_models_at_all_returns_none_for_both(self):
        result = _pick_5h_quota({"models": {}}, "Gemini 3.8 Flash (Medium)")
        self.assertIsNone(result["gemini_5h"])
        self.assertIsNone(result["claude_gpt_5h"])


if __name__ == "__main__":
    unittest.main()
