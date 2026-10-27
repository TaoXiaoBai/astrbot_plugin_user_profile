import asyncio
import copy
import json
import os
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_plugin
from test_plugin import Event, TagEngine, _new_stat
from profile_core import normalize_config, normalize_quote, sanitize_llm_tags
from risk_scoring import calculate_tag_components, merged_weights
from scene_profiles import cached_view


class ReviewScoringTests(unittest.TestCase):
    def test_config_default_valid_and_invalid(self):
        self.assertEqual(normalize_config({})["risk_review_level"], "中等")
        for level in ("低敏感", "中等", "高敏感"):
            self.assertEqual(normalize_config({"risk": {"risk_review_level": level}})["risk_review_level"], level)
        for value in (None, "unknown", [], 3):
            self.assertEqual(normalize_config({"risk_review_level": value})["risk_review_level"], "中等")

    def test_review_scores_and_management_are_independent(self):
        for level, expected in (("低敏感", 18), ("中等", 36), ("高敏感", 60)):
            weights = merged_weights(review_level=level)
            evidence = [{"tag": "nsfw_tendency", "confidence": 0.9}]
            parts = calculate_tag_components(evidence, weights)
            self.assertEqual(parts["semantic"], expected)
            self.assertEqual(calculate_tag_components(evidence * 3, weights), parts)
            self.assertEqual(calculate_tag_components([{"tag": "ban_history", "confidence": 1}], weights)["verified"], 40)

    def test_severity_confidence_and_explicit_zero(self):
        for severity, expected in (("mild", 20), ("clear", 40), ("severe", 60), ("invalid", 40)):
            evidence = [{"tag": "nsfw_tendency", "confidence": 1, "severity": severity}]
            self.assertEqual(calculate_tag_components(evidence)["semantic"], expected)
            self.assertEqual(calculate_tag_components(evidence, merged_weights({"nsfw_tendency": 0}, "高敏感"))["semantic"], 0)
        self.assertEqual(calculate_tag_components([{"tag": "nsfw_tendency", "confidence": 0.1, "severity": "clear"}])["semantic"], 4)
        self.assertEqual(calculate_tag_components([{"tag": "friendly", "confidence": 1}])["semantic"], 0)

    def test_severity_sanitization(self):
        for value, expected in (("mild", "mild"), ("severe", "severe"), (None, "clear"), ([], "clear")):
            parsed = sanitize_llm_tags([{"tag": "nsfw_tendency", "confidence": 0.9, "severity": value}])[0]
            self.assertEqual(parsed["severity"], expected)
            self.assertEqual(parsed["confidence"], 0.9)
        self.assertNotIn("severity", sanitize_llm_tags([{"tag": "friendly", "confidence": 1, "severity": "severe"}])[0])


class ReviewIntegrationTests(unittest.IsolatedAsyncioTestCase):
    make_plugin = test_plugin.PluginTests.make_plugin

    async def prepared(self):
        plugin = self.make_plugin({"history_scan_enabled": False, "friend_reconcile_enabled": False})
        await plugin._ensure_loaded()
        plugin._stats["11111"] = dict(_new_stat(), g_count=2)
        plugin._quotes["11111"] = [normalize_quote({"src": "群 22222", "t": 1, "text": "ANONYMOUS_MATERIAL"})]
        plugin._tag_engine.generate_llm_tags = AsyncMock(return_value={
            "tags": [{"tag": "nsfw_tendency", "confidence": 0.9, "severity": "clear", "reason": "主动索取不适宜内容"}],
            "impression": "LAST_GOOD", "traits": [],
        })
        return plugin

    async def test_switch_refreshes_immediately_and_same_level_reuses(self):
        plugin = await self.prepared()
        event = Event({})
        first = await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual(first["score"], 36)
        self.assertFalse(first["review_pending"])
        await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual(plugin._tag_engine.generate_llm_tags.await_count, 1)
        plugin.config["risk_review_level"] = "高敏感"
        self.assertTrue(cached_view(plugin, "11111", event)["review_pending"])
        second = await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual(second["score"], 60)
        self.assertFalse(second["review_pending"])
        self.assertEqual(plugin._tag_engine.generate_llm_tags.await_count, 2)
        call = plugin._tag_engine.generate_llm_tags.await_args
        self.assertEqual(call.args[5]["risk_review_level"], "高敏感")
        self.assertEqual(call.kwargs["prior"]["tags"], [])
        self.assertEqual(call.kwargs["prior"]["impression"], "")
        decision = await plugin.get_decision_profile("11111", event)
        self.assertEqual(decision["review_level"], "高敏感")
        self.assertFalse(decision["review_pending"])
        self.assertEqual(decision["tags"][0]["severity"], "clear")

    async def test_failure_retains_old_review_and_survives_restart(self):
        plugin = await self.prepared()
        event = Event({})
        await plugin.get_profile_tags_with_score("11111", event)
        plugin.config["risk_review_level"] = "高敏感"
        plugin._tag_engine.generate_llm_tags = AsyncMock(side_effect=RuntimeError("offline"))
        result = await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual(result["impression"], "LAST_GOOD")
        self.assertTrue(result["review_pending"])
        self.assertEqual(plugin._tags_cache["11111"]["slots"]["group"]["review_level"], "中等")
        await plugin.get_profile_tags_with_score("11111", event)
        plugin._tag_engine.generate_llm_tags.assert_awaited_once()
        restarted = self.make_plugin({"risk_review_level": "高敏感"})
        restarted._kv = copy.deepcopy(plugin._kv)
        await restarted._ensure_loaded()
        restored = cached_view(restarted, "11111", event)
        self.assertEqual(restored["impression"], "LAST_GOOD")
        self.assertTrue(restored["review_pending"])
        plugin.config["llm_failure_cache_ttl"] = 0
        plugin._tag_engine.generate_llm_tags = AsyncMock(return_value={"impression": "RECOVERED", "tags": [], "traits": []})
        self.assertFalse((await plugin.get_profile_tags_with_score("11111", event))["review_pending"])

    async def test_stale_inflight_response_cannot_overwrite_new_level(self):
        plugin = await self.prepared()
        started, release = asyncio.Event(), asyncio.Event()
        async def respond(*args, **kwargs):
            if args[5]["risk_review_level"] == "中等":
                started.set()
                await release.wait()
                return {"impression": "STALE"}
            return {"impression": "NEW"}
        plugin._tag_engine.generate_llm_tags = AsyncMock(side_effect=respond)
        task = asyncio.create_task(plugin.get_profile_tags_with_score("11111", Event({})))
        await asyncio.wait_for(started.wait(), 2)
        plugin.config["risk_review_level"] = "高敏感"
        await plugin.get_profile_tags_with_score("11111", Event({}))
        release.set()
        await task
        entry = plugin._tags_cache["11111"]["slots"]["group"]
        self.assertEqual(entry["impression"], "NEW")
        self.assertEqual(entry["review_level"], "高敏感")

    async def test_prompt_and_parse_chain_for_reasonable_and_improper_discussion(self):
        plugin = await self.prepared()
        for level in ("低敏感", "中等", "高敏感"):
            for tags in ([], [{"tag": "nsfw_tendency", "confidence": 0.9, "severity": "clear", "reason": "主动不当请求"}]):
                plugin.context.llm_generate = AsyncMock(return_value=types.SimpleNamespace(completion_text=json.dumps({"tags": tags, "impression": "语境判断", "traits": []})))
                parsed = await TagEngine(plugin.config).generate_llm_tags("11111", {}, plugin._quotes["11111"], [], plugin.context, dict(plugin.config, llm_provider_id="mock", risk_review_level=level))
                prompt = plugin.context.llm_generate.await_args.kwargs["prompt"]
                for text in (level, "合理医学", "引用反驳", "不得把bot回答", "证据可靠性", "severity"):
                    self.assertIn(text, prompt)
                self.assertEqual(bool(parsed["tags"]), bool(tags))
                score = calculate_tag_components(parsed["tags"], merged_weights(review_level=level))["semantic"]
                self.assertEqual(score, {"低敏感": 18, "中等": 36, "高敏感": 60}[level] if tags else 0)

    async def test_ordinary_model_tool_and_context_hide_review(self):
        plugin = await self.prepared()
        plugin._fetch_stranger_info = AsyncMock(return_value={})
        event = Event({})
        model = await plugin._build_profile_model("11111", event)
        self.assertFalse(model["audit_details"])
        self.assertNotIn("review_level", model)
        self.assertEqual(plugin._review_lines(model), [])
        text = plugin._profile_model_to_text(model)
        self.assertNotIn("审查：", text)
        self.assertNotIn("内容关注", text)
        tool = await plugin.user_profile_query(event, "11111")
        self.assertNotIn("已按本档审查", tool)
        self.assertNotIn("审查：", tool)
        context = await plugin._build_conversation_profile_context("11111", event)
        self.assertNotIn("审查：", context)
        plugin.config["risk_review_level"] = "高敏感"
        context = await plugin._build_conversation_profile_context("11111", event)
        self.assertNotIn("待复核", context)

    async def test_empty_switch_preserves_good_cache_and_memory_prior(self):
        plugin = await self.prepared()
        await plugin.get_profile_tags_with_score("11111", Event({}))
        entry = plugin._tags_cache["11111"]["slots"]["group"]
        entry["long_term_traits"] = [{"trait": "稳定兴趣", "confidence": 0.9}]
        plugin.config["risk_review_level"] = "低敏感"
        plugin._tag_engine.generate_llm_tags = AsyncMock(return_value={"tags": [], "impression": "", "traits": []})
        result = await plugin.get_profile_tags_with_score("11111", Event({}))
        self.assertEqual(result["impression"], "LAST_GOOD")
        self.assertTrue(result["review_pending"])
        prior = plugin._tag_engine.generate_llm_tags.await_args.kwargs["prior"]
        self.assertEqual(prior["tags"], [])
        self.assertEqual(prior["long_term_traits"][0]["trait"], "稳定兴趣")

    async def test_public_image_render_reopens_and_tag_toggle_collapses(self):
        from PIL import Image
        plugin = await self.prepared()
        plugin._fetch_stranger_info = AsyncMock(return_value={})
        model = await plugin._build_profile_model("11111", Event({}))
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            with patch("main.__file__", os.path.join(directory, "main.py")):
                path = plugin._render_profile_image(model)
                self.assertIsNotNone(path)
                with Image.open(path) as image:
                    image.load()
                    self.assertEqual(image.width, 960)
                    full_height = image.height
                plugin.config["card_show_tags"] = False
                compact = plugin._render_profile_image(model)
                self.assertIsNotNone(compact)
                with Image.open(compact) as image:
                    image.load()
                    self.assertLess(image.height, full_height)
