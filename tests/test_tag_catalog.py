import json
import types
import unittest
from unittest.mock import AsyncMock

from profile_core import LLM_TAGS, MAX_LLM_TAGS, sanitize_llm_tags
from risk_scoring import calculate_tag_score
from tag_catalog import TAG_CATALOG, SEMANTIC_RISK_TAGS, tag_prompt_catalog
from test_plugin import TagEngine, _tag_visual_category
from main import _tag_display


class TagCatalogTests(unittest.TestCase):
    def test_catalog_contains_120_readable_candidates_in_ten_dimensions(self):
        self.assertEqual(len(LLM_TAGS), 120)
        self.assertEqual(len({value[1] for value in TAG_CATALOG.values()}), 10)
        self.assertEqual(len({value[0] for value in TAG_CATALOG.values()}), 120)
        prompt = tag_prompt_catalog()
        for tag, (label, dimension) in TAG_CATALOG.items():
            self.assertIn(tag, prompt)
            self.assertIn(dimension, prompt)
            self.assertEqual(_tag_display(tag), label)
        self.assertEqual(_tag_visual_category("collaborative"), "正向")
        self.assertEqual(_tag_visual_category("gaming_interest"), "行为")

    def test_overflow_does_not_let_interests_crowd_out_other_dimensions(self):
        interest = [tag for tag, value in TAG_CATALOG.items() if value[1] == "兴趣话题"]
        self.assertGreater(len(interest), MAX_LLM_TAGS)
        # 高置信兴趣全部在前，仍须给有实际候选的其它维度保留位置。
        names = interest + ["concise", "helpful", "casual_chat", "hands_on_learning",
                            "tradeoff_aware", "accepts_refusal", "feedback_specific", "public_qa"]
        result = sanitize_llm_tags([
            {"tag": tag, "confidence": 0.95 if tag in interest else 0.8} for tag in names
        ])
        self.assertEqual(len(result), MAX_LLM_TAGS)
        self.assertEqual(len({TAG_CATALOG[item["tag"]][1] for item in result}), 9)
        self.assertEqual(sanitize_llm_tags(result), result)

    def test_round_robin_keeps_stronger_candidate_within_same_dimension(self):
        names = [tag for tag in TAG_CATALOG if tag not in SEMANTIC_RISK_TAGS]
        result = sanitize_llm_tags([
            {"tag": tag, "confidence": 1.0 if tag == "documentation_learning" else 0.1}
            for tag in names
        ])
        self.assertIn("documentation_learning", [item["tag"] for item in result])

    def test_small_candidate_set_is_not_padded_with_unobserved_dimensions(self):
        raw = [{"tag": "hands_on_learning", "confidence": 0.8}]
        result = sanitize_llm_tags(raw)
        self.assertEqual([item["tag"] for item in result], ["hands_on_learning"])

    def test_expanded_tags_survive_sanitization_and_keep_risk_priority(self):
        raw = [{"tag": tag, "confidence": 0.9, "reason": "行为依据"}
               for tag in LLM_TAGS if tag not in SEMANTIC_RISK_TAGS]
        raw += [{"tag": tag, "confidence": 0.8, "severity": "severe"}
                for tag in sorted(SEMANTIC_RISK_TAGS)]
        result = sanitize_llm_tags(raw)
        self.assertEqual(len(result), MAX_LLM_TAGS)
        self.assertTrue(SEMANTIC_RISK_TAGS <= {item["tag"] for item in result})
        self.assertEqual(next(item for item in result if item["tag"] == "nsfw_tendency")["severity"], "severe")
        self.assertEqual(sanitize_llm_tags(result), result)

    def test_more_than_five_tags_and_unknown_tags_are_handled(self):
        names = ["concise", "humorous", "coding_interest", "ai_interest",
                 "collaborative", "task_focused", "context_provider", "follow_up"]
        raw = [{"tag": name, "confidence": 0.8} for name in names]
        raw += [raw[0], {"tag": "invented_identity", "confidence": 1}]
        result = sanitize_llm_tags(raw)
        self.assertEqual([item["tag"] for item in result], names)

    def test_descriptive_tags_neither_add_nor_offset_risk(self):
        descriptive = [{"tag": tag, "confidence": 1} for tag in LLM_TAGS
                       if tag not in SEMANTIC_RISK_TAGS]
        self.assertEqual(calculate_tag_score(descriptive), 0)
        risk = [{"tag": "scam_suspect", "confidence": 1}]
        self.assertEqual(calculate_tag_score(risk + descriptive), calculate_tag_score(risk))


class TagPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_upgrade_refreshes_old_cache_and_failure_preserves_tags(self):
        from test_plugin import PluginTests, Event, _new_stat
        from profile_core import LLM_PROMPT_VERSION, normalize_quote
        from scene_profiles import cached_view

        plugin = PluginTests().make_plugin({
            "llm_provider_id": "mock", "llm_refresh_interval": 86400,
            "llm_failure_cache_ttl": 0, "history_scan_enabled": False,
        })
        await plugin._ensure_loaded()
        plugin._stats["11111"] = dict(_new_stat(), g_count=10)
        plugin._quotes["11111"] = [normalize_quote({"src": "群 22222", "text": "编程交流", "t": 1})]
        names = ["concise", "coding_interest", "ai_interest", "helpful",
                 "collaborative", "context_provider", "follow_up", "task_focused"]
        plugin._tag_engine.generate_llm_tags = AsyncMock(return_value={
            "tags": [{"tag": tag, "confidence": 0.9} for tag in names], "impression": "OLD",
        })
        event = Event({}, group="22222")
        await plugin.get_profile_tags("11111", event)
        entry = plugin._tags_cache["11111"]["slots"]["group"]
        entry["schema_version"] = LLM_PROMPT_VERSION - 1
        entry["fingerprint"] = "old_prompt"
        plugin._tag_engine.generate_llm_tags = AsyncMock(side_effect=RuntimeError("offline"))
        await plugin.get_profile_tags("11111", event)
        plugin._tag_engine.generate_llm_tags.assert_awaited_once()
        retained = cached_view(plugin, "11111", event)
        self.assertEqual(len(retained["tags"]), 8)
        self.assertEqual(retained["impression"], "OLD")
        self.assertTrue(retained["review_pending"])
        plugin._tag_engine.generate_llm_tags = AsyncMock(return_value={
            "tags": [{"tag": tag, "confidence": 0.9} for tag in names], "impression": "NEW",
        })
        await plugin.get_profile_tags("11111", event)
        plugin._tag_engine.generate_llm_tags.assert_awaited_once()
        self.assertEqual(cached_view(plugin, "11111", event)["impression"], "NEW")

    async def test_engine_uses_catalog_and_returns_expanded_tags(self):
        names = ["concise", "coding_interest", "ai_interest", "helpful",
                 "collaborative", "context_provider", "follow_up", "task_focused"]
        from tag_quality import quote_tag_ref
        quotes = [{"text": "我喜欢编程，会补充测试细节", "scene": "group"}]
        ref = quote_tag_ref("11111", quotes[0], {})
        response = {"tags": [{"tag": tag, "confidence": 0.9, "reason": "明确偏好或可观察行为",
                              "subject": "self", "evidence_refs": [ref],
                              "support_kind": "explicit_preference"} for tag in names],
                    "impression": "关注编程，主动补充背景"}
        context = types.SimpleNamespace(llm_generate=AsyncMock(return_value=types.SimpleNamespace(
            completion_text=json.dumps(response, ensure_ascii=False))))
        config = {"llm_provider_id": "mock"}
        result = await TagEngine(config).generate_llm_tags(
            "11111", {}, quotes, [], context, config)
        self.assertEqual(len(result["tags"]), 8)
        prompt = context.llm_generate.await_args.kwargs["prompt"]
        self.assertIn(f"0-{MAX_LLM_TAGS}", prompt)
        self.assertIn("数量是上限不是任务", prompt)
        self.assertIn("不以兴趣或表达习惯推导风险", prompt)
        self.assertIn("单次提及", prompt)
        self.assertIn("并非每个维度都必须有标签", prompt)
        self.assertIn("不能因没有正向标签反推", prompt)
        for dimension in {value[1] for value in TAG_CATALOG.values()}:
            self.assertIn(dimension, prompt)
        for tag in LLM_TAGS:
            self.assertIn(tag, prompt)
