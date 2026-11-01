import json
import re
import time
import types
import unittest
from unittest.mock import AsyncMock

from tests import test_plugin as fixtures
from profile_core import sanitize_llm_analysis, sanitize_long_term_traits
from risk_dimensions import material_refs, sanitize_events, merge_events, score_dimensions
from scene_profiles import slot_analysis, policy


class DimensionTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000
        self.quotes = [{"text": "sample", "t": self.now, "scene": "group", "group_id": "22222"}]
        self.material = material_refs("11111", self.quotes)
        self.ref = next(iter(self.material))

    def event(self, **changes):
        raw = dict(id="invented", episode_id="invented", dimension="content_boundary", severity="clear",
                   confidence=1.0, subject="self", evidence_refs=[self.ref], observed_at=self.now + 100)
        raw.update(changes)
        return sanitize_events([raw], self.material)

    def test_whitelist_refs_and_observation_time(self):
        event = self.event()[0]
        self.assertEqual(event["observed_at"], self.now)
        self.assertEqual(event["episode_id"], self.ref)
        self.assertNotEqual(event["id"], "invented")
        for changes in ({"evidence_refs": []}, {"evidence_refs": ["invented"]}, {"dimension": "personality"},
                        {"confidence": True}, {"confidence": "1"}, {"confidence": float("nan")}, {"severity": "unknown"}):
            self.assertEqual(self.event(**changes), [])

    def test_repeated_scan_no_persistence_and_no_clock_reset(self):
        first = self.event()
        merged = first
        for _ in range(20):
            merged = merge_events(merged, first, self.now + 100)
        self.assertEqual(len(merged), 1)
        result = score_dimensions(merged, self.now + 100, {"risk_semantic_cap": 100})
        self.assertEqual(result["risk_dimensions"]["content_boundary"]["episodes"], 1)
        self.assertEqual(merged[0]["observed_at"], self.now)
        self.assertEqual(score_dimensions(merged, self.now + 31 * 86400)["dimension_score"], 0)

    def test_third_person_uncertain_and_care_do_not_score(self):
        for subject in ("third_party", "uncertain"):
            self.assertEqual(score_dimensions(self.event(subject=subject), self.now)["dimension_score"], 0)
        cleaned = sanitize_llm_analysis({"safety_concerns": [{"kind": "dangerous_self_report", "reason": "private trauma"}],
                                         "risk_events": []})
        self.assertEqual(cleaned["safety_concerns"], [{"kind": "dangerous_self_report", "action": "care_only"}])
        self.assertNotIn("private trauma", str(cleaned))

    def test_severity_review_recency_and_explicit_zero(self):
        for severity, score in (("mild", 20), ("clear", 60), ("severe", 85), ("critical", 100)):
            self.assertEqual(score_dimensions(self.event(severity=severity), self.now, {"risk_semantic_cap": 100})["dimension_score"], score)
        self.assertEqual(score_dimensions(self.event(), self.now, {"risk_review_level": "低敏感", "risk_semantic_cap": 100})["dimension_score"], 51)
        self.assertEqual(score_dimensions(self.event(), self.now, {"risk_review_level": "高敏感", "risk_semantic_cap": 100})["dimension_score"], 69)
        self.assertEqual(score_dimensions(self.event(), self.now, {"risk_semantic_cap": 0})["dimension_score"], 0)
        self.assertEqual(score_dimensions(self.event(), self.now, {"risk_weights": {"nsfw_tendency": 0}})["dimension_score"], 0)
        self.assertEqual(score_dimensions(self.event(), self.now, {"risk_weights": '{"nsfw_tendency":20}'})["dimension_score"], 30)

    def test_independent_episodes_persistence_and_shared_cross_dimension(self):
        events = self.event()
        events.extend(self.event(dimension="fraud"))
        result = score_dimensions(events, self.now, {"risk_semantic_cap": 100})
        self.assertEqual(result["dimension_score"], 60)
        self.assertEqual(sum(d["episodes"] for d in result["risk_dimensions"].values()), 1)
        for index in range(1, 8):
            material = material_refs("11111", [dict(self.quotes[0], text=str(index), t=self.now - index * 3600)])
            events.extend(sanitize_events([dict(self.event()[0], evidence_refs=list(material))], material))
        result = score_dimensions(events, self.now, {"risk_semantic_cap": 100})
        self.assertEqual(result["dimension_score"], 78)
        self.assertEqual(max(d["persistence"] for d in result["risk_dimensions"].values()), 1.3)

    def test_sensitive_experience_is_not_long_term_trait(self):
        traits = sanitize_long_term_traits([{"trait": "喜欢音乐", "confidence": .9},
                                           {"trait": "经历性侵", "confidence": .9},
                                           {"trait": "个人背景", "confidence": .9, "reason": "未成年经历"}])
        self.assertEqual([t["trait"] for t in traits], ["喜欢音乐"])


class DimensionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_real_ref_validation_bounded_store_and_api(self):
        plugin = fixtures.PluginTests().make_plugin({"llm_provider_id": "mock", "history_scan_enabled": False,
                                                    "llm_refresh_interval": 0, "risk_semantic_cap": 100})
        await plugin._ensure_loaded()
        async def reply(**kwargs):
            refs = re.findall(r"ref=(r_[a-f0-9]{24})", kwargs["prompt"])
            return types.SimpleNamespace(completion_text=json.dumps({
                "tags": [], "impression": "表达直接", "traits": [],
                "risk_events": [{"dimension": "harassment", "severity": "clear", "confidence": .9,
                                 "subject": "self", "evidence_refs": refs, "observed_at": 999999999999},
                                {"dimension": "fraud", "severity": "critical", "confidence": 1,
                                 "subject": "self", "evidence_refs": ["invented"]}]}))
        plugin.context.llm_generate = AsyncMock(side_effect=reply)
        quotes = [{"scene": "group", "src": "群 22222", "t": int(time.time()), "text": "test message"}]
        generation = plugin._user_generation("11111")
        await slot_analysis(plugin, "11111", "group", quotes, {}, [], policy(plugin), generation)
        root = plugin._tags_cache["11111"]
        event = root["risk_evidence"]["group"]["events"][0]
        self.assertEqual(event["observed_at"], quotes[0]["t"])
        self.assertEqual(len(root["risk_evidence"]["group"]["events"]), 1)
        self.assertNotIn("test message", json.dumps(root["risk_evidence"]))
        plugin.get_profile_tags = AsyncMock(return_value=[])
        result = await plugin.get_profile_tags_with_score("11111", fixtures.Event({}))
        self.assertGreater(result["dimension_score"], 0)
        self.assertIsNone(result["compat_score"])
        self.assertEqual(result["dimension_score"], result["score"])
        self.assertEqual(result["risk_status"], "ready")
        public = plugin._public_profile_view(result, fixtures.Event({}))
        self.assertNotIn("risk_events", public)
        self.assertNotIn("risk_dimensions", public)
        # Evidence survives successful later scans that no longer contain the event.
        plugin.context.llm_generate = AsyncMock(return_value=types.SimpleNamespace(completion_text='{"impression":"新印象","risk_events":[]}'))
        await slot_analysis(plugin, "11111", "group", [dict(quotes[0], text="unrelated")], {}, [], policy(plugin), generation)
        self.assertEqual(len(root["risk_evidence"]["group"]["events"]), 1)
        plugin.config["risk_weights"] = {"troll": 0}
        self.assertEqual((await plugin.get_profile_tags_with_score("11111", fixtures.Event({})))["dimension_score"], 0)

    async def test_private_evidence_not_visible_to_group(self):
        plugin = fixtures.PluginTests().make_plugin({"history_scan_enabled": False})
        await plugin._ensure_loaded()
        now = int(time.time())
        material = material_refs("11111", [{"text": "private", "scene": "private", "t": now}])
        events = sanitize_events([dict(dimension="fraud", severity="severe", confidence=1,
                                      subject="self", evidence_refs=list(material))], material)
        plugin._tags_cache["11111"] = {"slots": {}, "risk_evidence": {"private": {"policy": list(policy(plugin)), "events": events}}}
        plugin.get_profile_tags = AsyncMock(return_value=[])
        result = await plugin.get_profile_tags_with_score("11111", fixtures.Event({}))
        self.assertEqual(result["dimension_score"], 0)
