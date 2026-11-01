import copy
import json
import re
import types
import unittest
from unittest.mock import AsyncMock

import test_plugin
from profile_core import sanitize_llm_tags
from risk_dimensions import material_refs, sanitize_events
from scene_profiles import cached_view, policy
from tag_quality import ground_generated_tags, quote_tag_ref


class TagQualityTests(unittest.TestCase):
    def setUp(self):
        self.quote = {"text": "我喜欢编程", "scene": "group", "group_id": "22222", "t": 100}
        self.ref = next(iter(material_refs("11111", [self.quote])))
        self.other = "r_" + "a" * 24
        self.materials = {self.ref: self.quote, self.other: dict(self.quote, text="我最近在写脚本")}

    def candidate(self, tag="coding_interest", **overrides):
        return dict(tag=tag, confidence=0.9, reason="明确表达偏好", subject="self",
                    evidence_refs=[self.ref], support_kind="explicit_preference", **overrides)

    def test_forged_missing_and_cross_scene_refs_are_rejected(self):
        for refs in ([], ["r_" + "f" * 24], [self.ref, "forged"], "not-array", [[self.ref]]):
            item = self.candidate()
            item["evidence_refs"] = refs
            self.assertEqual(ground_generated_tags([item], self.materials), [])
        private = dict(self.quote, scene="private", group_id="")
        private_ref = next(iter(material_refs("11111", [private])))
        item = self.candidate()
        item["evidence_refs"] = [private_ref]
        self.assertEqual(ground_generated_tags([item], self.materials), [])

    def test_subject_confidence_and_reason_are_checked(self):
        for key, values in (("subject", [None, "third_party", "uncertain"]),
                            ("confidence", [True, False, float("nan"), float("inf"), -1, 2, "0.9"]),
                            ("reason", [None, "", "   "])):
            for value in values:
                item = self.candidate()
                item[key] = value
                self.assertEqual(ground_generated_tags([item], self.materials), [])
        self.assertEqual(ground_generated_tags([{"tag": []}, None], self.materials), [])

    def test_interest_requires_preference_or_distinct_discussion(self):
        item = self.candidate()
        self.assertEqual(len(ground_generated_tags([item], self.materials)), 1)
        item["support_kind"] = "observed_behavior"
        self.assertEqual(ground_generated_tags([item], self.materials), [])
        item["evidence_refs"] = [self.ref, self.other]
        self.assertEqual(len(ground_generated_tags([item], self.materials)), 1)
        repeated = {self.ref: self.quote, self.other: dict(self.quote, t=200)}
        self.assertEqual(ground_generated_tags([item], repeated), [])

    def test_descriptive_threshold_does_not_lower_existing_risk_sensitivity(self):
        descriptive = self.candidate("concise")
        descriptive["confidence"] = 0.59
        self.assertEqual(ground_generated_tags([descriptive], self.materials), [])
        descriptive["confidence"] = 0.6
        self.assertEqual(len(ground_generated_tags([descriptive], self.materials)), 1)
        risk = self.candidate("troll")
        risk["confidence"] = 0.2
        self.assertEqual(len(ground_generated_tags([risk], self.materials)), 1)

    def test_duplicate_tags_use_stronger_evidence_not_first_result(self):
        for values in ([0.2, 0.9], [0.9, 0.2]):
            result = sanitize_llm_tags([{"tag": "friendly", "confidence": value} for value in values])
            self.assertEqual(result[0]["confidence"], 0.9)
        result = sanitize_llm_tags([
            {"tag": "nsfw_tendency", "confidence": 0.95, "severity": "mild"},
            {"tag": "nsfw_tendency", "confidence": 0.8, "severity": "severe"},
        ])
        self.assertEqual(result[0]["severity"], "severe")
        self.assertEqual(sanitize_llm_tags([{"tag": "friendly", "confidence": True}]), [])

    def test_parent_only_removed_when_all_evidence_is_covered(self):
        parent = {"tag": "tech_interest", "confidence": 0.8, "evidence_refs": [self.ref]}
        child = {"tag": "coding_interest", "confidence": 0.9, "evidence_refs": [self.ref]}
        self.assertEqual([item["tag"] for item in sanitize_llm_tags([parent, child])], ["coding_interest"])
        parent["evidence_refs"] = [self.ref, self.other]
        self.assertEqual(len(sanitize_llm_tags([parent, child])), 2)
        parent.pop("evidence_refs")
        self.assertEqual(len(sanitize_llm_tags([parent, child])), 2)

    def test_normal_is_only_fallback_and_preferences_are_not_mutually_exclusive(self):
        names = ["normal", "brief_answer_preference", "step_by_step_preference"]
        result = sanitize_llm_tags([{"tag": name, "confidence": 0.8} for name in names])
        self.assertEqual([item["tag"] for item in result], names[1:])
        risk = sanitize_llm_tags([{"tag": name, "confidence": 0.8} for name in ("normal", "troll")])
        self.assertEqual([item["tag"] for item in risk], ["troll"])

    def test_missing_time_has_stable_non_timed_ref_without_risk_event(self):
        quote = dict(self.quote, t=0)
        ref = quote_tag_ref("11111", quote, {})
        self.assertTrue(ref.startswith("t_"))
        self.assertEqual(ref, quote_tag_ref("11111", quote, {}))
        self.assertNotEqual(ref, quote_tag_ref("22222", quote, {}))
        self.assertNotEqual(ref, quote_tag_ref("11111", dict(quote, scene="private"), {}))
        self.assertEqual(sanitize_events([dict(dimension="fraud", severity="clear", confidence=1,
                                              subject="self", evidence_refs=[ref])], {ref: quote}), [])


class TagQualityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    make_plugin = test_plugin.PluginTests.make_plugin

    async def test_budget_prefers_recent_and_does_not_accept_unselected_refs(self):
        plugin = self.make_plugin({"llm_provider_id": "mock", "llm_material_max_chars": 500})
        quotes = [{"text": "OLD" + "x" * 190, "t": index + 1, "scene": "group"} for index in range(20)]
        quotes[-1] = {"text": "NEWEST明确偏好", "t": 30, "scene": "group"}
        old_ref = next(iter(material_refs("11111", [quotes[0]])))

        async def respond(**kwargs):
            prompt = kwargs["prompt"]
            block = prompt.split("<untrusted_evidence>\n", 1)[1].split("\n</untrusted_evidence>", 1)[0]
            self.assertLessEqual(len(block), 500)
            self.assertIn("NEWEST", block)
            self.assertNotIn("ref=" + old_ref, block)
            ref = re.findall(r"ref=([rt]_[a-f0-9]{24})", block)[-1]
            return types.SimpleNamespace(completion_text=json.dumps({"tags": [
                {"tag": "friendly", "confidence": 0.9, "reason": "行为描述", "subject": "self", "evidence_refs": [ref]},
                {"tag": "troll", "confidence": 1, "reason": "未选材料", "subject": "self", "evidence_refs": [old_ref]},
            ]}))
        plugin.context.llm_generate = AsyncMock(side_effect=respond)
        result = await plugin._tag_engine.generate_llm_tags("11111", {}, quotes, [], plugin.context, plugin.config)
        self.assertEqual([item["tag"] for item in result["tags"]], ["friendly"])

    async def test_unverifiable_response_preserves_successful_cache(self):
        plugin = self.make_plugin({"llm_provider_id": "mock", "history_scan_enabled": False,
                                   "llm_refresh_interval": 0, "llm_tag_cache_ttl": 0})
        await plugin._ensure_loaded()
        plugin._stats["11111"] = dict(test_plugin._new_stat(), g_count=10)
        plugin._quotes["11111"] = [{"src": "群 22222", "scene": "group", "text": "HELLO", "t": 100}]
        plugin._tag_engine.generate_llm_tags = AsyncMock(return_value={
            "tags": [{"tag": "friendly", "confidence": 0.9}], "impression": "LAST_GOOD"})
        event = test_plugin.Event({})
        await plugin.get_profile_tags("11111", event)
        # 下一次走真实生成器，模型漏掉依据，不能用空标签覆盖旧成功画像。
        del plugin._tag_engine.generate_llm_tags
        plugin.context.llm_generate = AsyncMock(return_value=types.SimpleNamespace(completion_text=json.dumps({
            "tags": [{"tag": "troll", "confidence": 1, "reason": "无引用"}], "impression": "UNVERIFIED"})))
        await plugin.get_profile_tags("11111", event)
        retained = cached_view(plugin, "11111", event)
        self.assertEqual(retained["impression"], "LAST_GOOD")
        self.assertEqual(retained["tags"][0]["tag"], "friendly")
        self.assertEqual(plugin._tags_cache["11111"]["slots"]["group"]["refresh_status"], "error")

    async def test_internal_refs_are_not_public_and_projection_does_not_mutate(self):
        plugin = self.make_plugin()
        result = {"tags": [{"tag": "friendly", "confidence": 0.9, "evidence_refs": ["t_" + "a" * 24],
                            "support_kind": "observed_behavior", "evidence": "PRIVATE"}]}
        before = copy.deepcopy(result)
        view = plugin._public_profile_view(result, test_plugin.Event({}))
        self.assertNotIn("evidence_refs", view["tags"][0])
        self.assertNotIn("support_kind", view["tags"][0])
        self.assertEqual(result, before)

    async def test_fused_duplicate_uses_severity_and_retains_scene_isolation(self):
        plugin = self.make_plugin({"cross_scene_link_enabled": True})
        await plugin._ensure_loaded()
        def entry(severity, confidence):
            return {"policy": list(policy(plugin)), "status": "success", "tags": [
                {"tag": "nsfw_tendency", "confidence": confidence, "severity": severity}]}
        plugin._tags_cache["11111"] = {"slots": {"group": entry("mild", 0.95), "private": entry("severe", 0.8)}}
        fused = cached_view(plugin, "11111", test_plugin.Event({}, group=""))
        group = cached_view(plugin, "11111", test_plugin.Event({}))
        self.assertEqual(fused["tags"][0]["severity"], "severe")
        self.assertEqual(group["tags"][0]["severity"], "mild")

    async def test_fused_normal_fallback_is_removed_without_changing_public_scene(self):
        plugin = self.make_plugin({"cross_scene_link_enabled": True})
        await plugin._ensure_loaded()
        def entry(tag):
            return {"policy": list(policy(plugin)), "status": "success",
                    "tags": [{"tag": tag, "confidence": 0.9}]}
        plugin._tags_cache["11111"] = {"slots": {"group": entry("normal"), "private": entry("troll")}}
        before = copy.deepcopy(plugin._tags_cache)
        fused = cached_view(plugin, "11111", test_plugin.Event({}, group=""))
        group = cached_view(plugin, "11111", test_plugin.Event({}))
        self.assertEqual([item["tag"] for item in fused["tags"]], ["troll"])
        self.assertEqual([item["tag"] for item in group["tags"]], ["normal"])
        self.assertEqual(plugin._tags_cache, before)

    async def test_fusion_preserves_different_evidence_and_does_not_reapply_branch_cap(self):
        from tag_catalog import TAG_CATALOG, SEMANTIC_RISK_TAGS, MAX_LLM_TAGS
        plugin = self.make_plugin({"cross_scene_link_enabled": True})
        await plugin._ensure_loaded()
        names = [tag for tag in TAG_CATALOG if tag not in SEMANTIC_RISK_TAGS and tag != "normal"]
        def entry(selected, ref):
            return {"policy": list(policy(plugin)), "status": "success", "tags": [
                {"tag": tag, "confidence": 0.9, "evidence_refs": [ref]} for tag in selected]}
        group_ref, private_ref = "r_" + "a" * 24, "r_" + "b" * 24
        # 泛化与具体标签依据不同，不应跨场景误删；两侧各24个，不应融合后截成24个。
        group_names = ["tech_interest"] + [tag for tag in names if tag not in ("tech_interest", "coding_interest")][:MAX_LLM_TAGS - 1]
        private_names = ["coding_interest"] + [tag for tag in names if tag not in group_names and tag != "coding_interest"][:MAX_LLM_TAGS - 1]
        # 不带ref避免同一侧具体化清理影响对融合上限的测试。
        group_entry, private_entry = entry(group_names, group_ref), entry(private_names, private_ref)
        for item in group_entry["tags"] + private_entry["tags"]:
            if item["tag"] not in ("tech_interest", "coding_interest"):
                item.pop("evidence_refs")
        plugin._tags_cache["11111"] = {"slots": {"group": group_entry, "private": private_entry}}
        result = cached_view(plugin, "11111", test_plugin.Event({}, group=""))
        tags = {item["tag"] for item in result["tags"]}
        self.assertEqual(len(tags), MAX_LLM_TAGS * 2)
        self.assertIn("tech_interest", tags)
        self.assertIn("coding_interest", tags)
