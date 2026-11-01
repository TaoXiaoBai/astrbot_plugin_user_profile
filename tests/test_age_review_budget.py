import json
import time
import unittest

from profile_core import normalize_config
from tests import test_age_protection as age_fixtures
from tests import test_age_review_diagnostics as diagnostics_fixtures


class AgeReviewBudgetTests(unittest.IsolatedAsyncioTestCase):
    def test_defaults_and_configuration_boundaries(self):
        defaults = normalize_config({})
        self.assertEqual(defaults["age_protection_max_chars"], 24000)
        self.assertEqual(defaults["age_protection_timeout_seconds"], 180)
        for chars, seconds, expected_chars, expected_seconds in (
            (0, 0, 500, 1), (500, 1, 500, 1),
            (64000, 600, 64000, 600), (99999, 999, 64000, 600),
            (48000, 400, 48000, 400), ("bad", None, 24000, 180),
        ):
            config = normalize_config({"age_protection": {
                "age_protection_max_chars": chars, "age_protection_timeout_seconds": seconds}})
            self.assertEqual(config["age_protection_max_chars"], expected_chars)
            self.assertEqual(config["age_protection_timeout_seconds"], expected_seconds)

    async def test_over_12000_characters_reach_model_without_old_caps(self):
        fixture = diagnostics_fixtures.AgeReviewDiagnosticsTests()
        for budget in (24000, 64000):
            plugin = fixture.plugin(age_protection_use_qq_age=False, age_protection_max_chars=budget)
            texts = ["我今年十七岁" + str(i) + "甲" * 6990 for i in range(2 if budget == 24000 else 8)]
            fixture.history(plugin, [dict(role="user", content=text, timestamp=int(time.time())) for text in texts])
            result = await plugin.assess_age_protection("11111", age_fixtures.AgeProtectionTests.event("你好"))
            payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"].split("\n", 1)[1])
            sent = [m["text"] for m in payload["materials"]]
            self.assertTrue(all(text in sent for text in texts))
            self.assertGreater(sum(map(len, sent)), 12000)
            self.assertLessEqual(sum(map(len, sent)), budget)
            self.assertFalse(payload["collection"]["material_truncated"])
            self.assertTrue(result["decision_eligible"])

    async def test_current_message_and_candidate_share_segment_cap(self):
        fixture = diagnostics_fixtures.AgeReviewDiagnosticsTests()
        for budget, size in ((24000, 8000), (500, 500)):
            for extra in (0, 1):
                plugin = fixture.plugin(history_scan_enabled=False, age_protection_use_qq_age=False,
                                        age_protection_max_chars=budget)
                text = "我十七岁" + "甲" * (size + extra - 4)
                result = await plugin.assess_age_protection("11111", age_fixtures.AgeProtectionTests.event(text))
                payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"].split("\n", 1)[1])
                self.assertEqual(len(payload["materials"][0]["text"]), size)
                self.assertEqual(payload["collection"]["current_truncated"], bool(extra))
                self.assertEqual(payload["collection"]["candidate_truncated"], bool(extra))
                self.assertEqual(result["decision_eligible"], not bool(extra))

    async def test_same_message_adult_tail_blocks_even_when_truncated_or_omitted(self):
        fixture = diagnostics_fixtures.AgeReviewDiagnosticsTests()
        for current in (False, True):
            for budget, padding in ((24000, 2000), (24000, 9000), (500, 900)):
                plugin = fixture.plugin(age_protection_use_qq_age=False, age_protection_max_chars=budget)
                text = "我十七岁。" + "甲" * padding + "我已经成年了"
                fixture.history(plugin, [] if current else [dict(role="user", content=text, timestamp=int(time.time()))])
                result = await plugin.assess_age_protection("11111", age_fixtures.AgeProtectionTests.event(text if current else "你好"))
                self.assertFalse(result["decision_eligible"], (current, budget, padding))
                if padding == 2000:
                    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"].split("\n", 1)[1])
                    self.assertIn("我已经成年了", str(payload["materials"]))
                    self.assertTrue(result["identity_conflict"])
                else:
                    self.assertIn("age_candidates_incomplete", result["diagnostics"]["decision"]["reasons"])

    async def test_low_total_budget_omission_still_blocks_execution(self):
        fixture = diagnostics_fixtures.AgeReviewDiagnosticsTests()
        plugin = fixture.plugin(age_protection_use_qq_age=False, age_protection_max_chars=500)
        fixture.history(plugin, [dict(role="user", content="我十七岁" + "甲" * 390, timestamp=int(time.time())),
                                 dict(role="user", content="我已经成年了" + "乙" * 390, timestamp=int(time.time()))])
        result = await plugin.assess_age_protection("11111", age_fixtures.AgeProtectionTests.event("你好"))
        payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"].split("\n", 1)[1])
        self.assertLessEqual(sum(len(m["text"]) for m in payload["materials"]), 500)
        self.assertTrue(payload["collection"]["candidate_truncated"])
        self.assertFalse(result["decision_eligible"])
