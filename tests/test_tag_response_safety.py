import copy
import json
import re
import types
import unittest
from unittest.mock import AsyncMock

import test_plugin
from scene_profiles import cached_view
from tag_quality import ground_generated_tags, validate_generated_tags


class TagResponseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.ref = "r_" + "a" * 24
        self.second = "r_" + "b" * 24
        self.materials = {self.ref: {"text": "我喜欢 Python!"}, self.second: {"text": "我 喜欢 Ｐｙｔｈｏｎ！"}}

    def item(self, tag="friendly", **overrides):
        value = dict(tag=tag, confidence=0.9, reason="行为依据", subject="self", evidence_refs=[self.ref])
        return dict(value, **overrides)

    def test_mixed_positive_and_invalid_risk_response_fails_as_whole(self):
        for invalid in (self.item("troll", evidence_refs=[]), self.item("troll", subject="third_party"),
                        self.item("scam_suspect", confidence=True), self.item("nsfw_tendency", reason="\x00\u200b")):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "profile_risk_tag_without_grounded_evidence"):
                    validate_generated_tags([self.item(), invalid], self.materials)

    def test_unknown_items_and_overflow_do_not_silently_disappear(self):
        for invalid in (None, "friendly", {}, self.item(tag="invented"), self.item(tag=[])):
            with self.assertRaisesRegex(ValueError, "profile_unknown_or_malformed_tag"):
                validate_generated_tags([self.item(), invalid], self.materials)
        with self.assertRaisesRegex(ValueError, "profile_tags_exceed_candidate_budget"):
            validate_generated_tags([self.item()] * 241, self.materials)

    def test_weak_descriptive_candidate_does_not_invalidate_grounded_risk(self):
        result = validate_generated_tags([self.item(confidence=0.2), self.item("troll", confidence=0.2)], self.materials)
        self.assertEqual([item["tag"] for item in result], ["troll"])

    def test_cosmetic_variants_are_not_independent_interest_evidence(self):
        item = self.item("coding_interest", evidence_refs=[self.ref, self.second])
        self.assertEqual(ground_generated_tags([item], self.materials), [])
        different = dict(self.materials, **{self.second: {"text": "我最近用 Python 写工具"}})
        self.assertEqual(len(ground_generated_tags([item], different)), 1)
        explicit = self.item("coding_interest", support_kind="explicit_preference")
        self.assertEqual(len(ground_generated_tags([explicit], self.materials)), 1)

    def test_reason_controls_and_huge_confidence_are_rejected_safely(self):
        self.assertEqual(ground_generated_tags([self.item(reason="\x00\u200b")], self.materials), [])
        self.assertEqual(ground_generated_tags([self.item(confidence=10 ** 500)], self.materials), [])
        original = self.item(reason="正常\x00\u200b理由")
        saved = copy.deepcopy(original)
        self.assertEqual(ground_generated_tags([original], self.materials)[0]["reason"], "正常理由")
        self.assertEqual(original, saved)


class TagResponseSafetyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_invalid_risk_cannot_replace_old_risk_with_positive(self):
        plugin = test_plugin.PluginTests().make_plugin({"llm_provider_id": "mock", "history_scan_enabled": False,
                                                        "llm_refresh_interval": 0, "llm_tag_cache_ttl": 0})
        await plugin._ensure_loaded()
        plugin._stats["11111"] = dict(test_plugin._new_stat(), g_count=10)
        plugin._quotes["11111"] = [{"src": "群 22222", "scene": "group", "text": "test", "t": 100}]
        event = test_plugin.Event({})
        original_generator = plugin._tag_engine.generate_llm_tags
        plugin._tag_engine.generate_llm_tags = AsyncMock(return_value={
            "tags": [{"tag": "troll", "confidence": 0.9}], "impression": "LAST_GOOD"})
        await plugin.get_profile_tags("11111", event)
        plugin._tag_engine.generate_llm_tags = original_generator

        async def reply(**kwargs):
            ref = re.findall(r"ref=([rt]_[a-f0-9]{24})", kwargs["prompt"])[0]
            return types.SimpleNamespace(completion_text=json.dumps({"tags": [
                {"tag": "friendly", "confidence": 0.9, "reason": "有效", "subject": "self", "evidence_refs": [ref]},
                {"tag": "troll", "confidence": 0.9, "reason": "无效", "subject": "self", "evidence_refs": []},
            ], "impression": "DO_NOT_REPLACE"}))
        plugin.context.llm_generate = AsyncMock(side_effect=reply)
        await plugin.get_profile_tags("11111", event)
        retained = cached_view(plugin, "11111", event)
        self.assertEqual(retained["impression"], "LAST_GOOD")
        self.assertEqual([item["tag"] for item in retained["tags"]], ["troll"])
        self.assertEqual(plugin._tags_cache["11111"]["slots"]["group"]["refresh_status"], "error")
