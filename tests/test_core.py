import math
import unittest

from profile_core import (
    LLM_TAGS,
    build_material_fingerprint,
    normalize_config,
    sanitize_llm_tags,
    speaker_lines,
)


class CoreTests(unittest.TestCase):
    def test_legacy_config_and_zero_values_are_preserved(self):
        cfg = normalize_config({
            "self_query_only": True,
            "history_scan_cooldown": 0,
            "llm_tag_cache_ttl": 0,
            "risk_level_low": 90,
            "risk_level_high": 10,
            "risk_level_extreme": float("inf"),
        })
        self.assertFalse(cfg["allow_other_query"])
        self.assertEqual(cfg["history_scan_cooldown"], 0)
        self.assertEqual(cfg["llm_tag_cache_ttl"], 0)
        self.assertLessEqual(cfg["risk_level_low"], cfg["risk_level_high"])
        self.assertLessEqual(cfg["risk_level_high"], cfg["risk_level_extreme"])
        self.assertTrue(math.isfinite(cfg["risk_level_extreme"]))

    def test_new_privacy_defaults_preserve_existing_behavior(self):
        cfg = normalize_config({})
        self.assertTrue(cfg["store_quotes"])
        self.assertTrue(cfg["store_private_quotes"])
        self.assertTrue(cfg["llm_include_private_quotes"])
        self.assertEqual(cfg["quote_retention_days"], 0)

    def test_llm_tags_are_allowlisted_and_sanitized(self):
        result = sanitize_llm_tags([
            {"tag": "friendly", "confidence": 2, "reason": "ok\x00\n" + "x" * 400},
            {"tag": "invented", "confidence": 0.5, "reason": "bad"},
            {"tag": "scam_suspect", "confidence": "nan", "reason": "bad"},
        ])
        self.assertEqual(len(result), 1)
        self.assertIn(result[0]["tag"], LLM_TAGS)
        self.assertEqual(result[0]["confidence"], 1.0)
        self.assertNotIn("\x00", result[0]["evidence"])
        self.assertLessEqual(len(result[0]["evidence"]), 160)

    def test_fingerprint_changes_with_all_prompt_inputs(self):
        quotes = [{"text": "a"}]
        stats = {"g_count": 1, "images": 0}
        tags = [{"tag": "active_low", "confidence": 0.8, "source": "stats"}]
        base = build_material_fingerprint(quotes, "p", 2, stats, tags)
        self.assertNotEqual(base, build_material_fingerprint([{"text": "b"}], "p", 2, stats, tags))
        self.assertNotEqual(base, build_material_fingerprint(quotes, "q", 2, stats, tags))
        self.assertNotEqual(base, build_material_fingerprint(quotes, "p", 3, stats, tags))
        self.assertNotEqual(base, build_material_fingerprint(
            quotes, "p", 2, {"g_count": 1, "images": 1}, tags
        ))
        self.assertNotEqual(base, build_material_fingerprint(
            quotes, "p", 2, stats,
            [{"tag": "active_medium", "confidence": 0.8, "source": "stats"}],
        ))

    def test_history_speaker_id_is_exact(self):
        text = "A (ID: 12345): yes\nB (ID: 912345): no\nID: 123456 wrong"
        self.assertEqual(speaker_lines(text, "12345"), ["yes"])


if __name__ == "__main__":
    unittest.main()
