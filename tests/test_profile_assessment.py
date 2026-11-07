import time
import unittest
from unittest.mock import AsyncMock

import test_plugin
from test_plugin import Event, _new_stat
from profile_core import LLM_PROMPT_VERSION, normalize_quote
from risk_dimensions import material_refs, sanitize_events
from scene_profiles import policy


class ProfileAssessmentTests(unittest.IsolatedAsyncioTestCase):
    make_plugin = test_plugin.PluginTests.make_plugin

    async def prepared(self, **config):
        plugin = self.make_plugin({
            "history_scan_enabled": False, "friend_reconcile_enabled": False,
            "link_qq_tools_ban": False, **config,
        })
        await plugin._ensure_loaded()
        plugin._stats["11111"] = dict(_new_stat(), g_count=2)
        plugin._quotes["11111"] = [normalize_quote({
            "src": "群 22222", "text": "普通交流材料", "t": int(time.time()),
        })]
        return plugin

    def install_slot(self, plugin, scene, tags):
        entry = {
            "status": "success", "time": int(time.time()), "policy": list(policy(plugin)),
            "schema_version": LLM_PROMPT_VERSION, "review_level": plugin.config["risk_review_level"],
            "tags": tags, "impression": scene + " impression", "traits": [],
        }
        plugin._tags_cache.setdefault("11111", {}).setdefault("slots", {})[scene] = entry
        return entry

    def install_canonical(self, plugin, scene="group"):
        materials = material_refs("11111", [{
            "text": scene + " evidence", "t": int(time.time()), "scene": scene,
        }])
        events = sanitize_events([{
            "dimension": "content_boundary", "severity": "mild", "confidence": 1,
            "subject": "self", "evidence_refs": list(materials),
        }], materials)
        plugin._tags_cache.setdefault("11111", {}).setdefault("risk_evidence", {})[scene] = {
            "policy": list(policy(plugin)), "events": events,
        }

    async def test_statistics_without_llm_are_unreviewed_not_zero(self):
        plugin = await self.prepared(llm_tags=False)
        plugin.context.llm_generate = AsyncMock(side_effect=AssertionError("unexpected LLM"))
        result = await plugin.get_profile_tags_with_score("11111", Event({}))
        self.assertIn("active_low", [item["tag"] for item in result["tags"]])
        self.assertIsNone(result["score"])
        self.assertEqual(result["risk_status"], "unreviewed")
        decision = await plugin.get_decision_profile("11111", Event({}))
        self.assertIsNone(decision["score"])
        self.assertEqual(decision["risk_status"], "unreviewed")
        plugin.context.llm_generate.assert_not_awaited()

    async def test_successful_zero_review_is_ready_and_decision_exposes_semantics(self):
        plugin = await self.prepared()
        plugin._tag_engine.generate_llm_tags = AsyncMock(return_value={
            "tags": [], "traits": [], "impression": "普通交流，无风险材料", "risk_events": [],
        })
        result = await plugin.get_profile_tags_with_score("11111", Event({}))
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["risk_status"], "ready")
        self.assertFalse(result["review_pending"])
        plugin._tag_engine.generate_llm_tags.assert_awaited_once()
        decision = await plugin.get_decision_profile("11111", Event({}))
        self.assertEqual(decision["schema_version"], 5)
        self.assertEqual(decision["semantic_status"], "success")
        self.assertEqual(decision["semantic_source"], "risk_events")
        self.assertEqual(decision["semantic_time"], plugin._tags_cache["11111"]["slots"]["group"]["time"])
        self.assertGreater(decision["semantic_time"], 0)
        self.assertEqual(decision["score"], 0)
        self.assertEqual(decision["risk_status"], "ready")

    async def test_failed_refresh_preserves_successful_zero_as_stale(self):
        plugin = await self.prepared(llm_tag_cache_ttl=0, llm_refresh_interval=0, llm_failure_cache_ttl=0)
        plugin._tag_engine.generate_llm_tags = AsyncMock(return_value={
            "tags": [], "traits": [], "impression": "最后成功的普通印象", "risk_events": [],
        })
        first = await plugin.get_profile_tags_with_score("11111", Event({}))
        self.assertEqual((first["score"], first["risk_status"]), (0, "ready"))
        success_time = first["semantic_time"]
        plugin._tag_engine.generate_llm_tags = AsyncMock(side_effect=RuntimeError("offline"))
        stale = await plugin.get_profile_tags_with_score("11111", Event({}))
        self.assertEqual((stale["score"], stale["risk_status"]), (0, "stale"))
        self.assertEqual(stale["impression"], first["impression"])
        self.assertEqual(stale["semantic_time"], success_time)
        self.assertEqual(stale["semantic_status"], "success")
        self.assertTrue(stale["review_pending"])
        self.assertEqual(plugin._tags_cache["11111"]["slots"]["group"]["refresh_status"], "error")
        plugin._tag_engine.generate_llm_tags.assert_awaited_once()

    async def test_schema_and_review_changes_make_successful_zero_stale(self):
        plugin = await self.prepared(llm_tags=False)
        slot = self.install_slot(plugin, "group", [{"tag": "friendly", "confidence": 1}])
        fresh = await plugin.get_profile_tags_with_score("11111", Event({}))
        self.assertEqual((fresh["score"], fresh["risk_status"]), (0, "ready"))
        for field, value in (("schema_version", LLM_PROMPT_VERSION - 1), ("review_level", "高敏感")):
            with self.subTest(field=field):
                old = slot[field]
                slot[field] = value
                stale = await plugin.get_profile_tags_with_score("11111", Event({}))
                self.assertEqual((stale["score"], stale["risk_status"]), (0, "stale"))
                slot[field] = old

    async def test_canonical_event_replaces_same_scene_legacy_tag(self):
        plugin = await self.prepared(risk_semantic_cap=100)
        plugin._tag_engine.generate_llm_tags = AsyncMock(side_effect=AssertionError("fresh cache must be reused"))
        tags = [{"tag": "scam_suspect", "confidence": 1}]
        self.install_slot(plugin, "group", tags)
        self.install_canonical(plugin)
        result = await plugin.get_profile_tags_with_score("11111", Event({}))
        self.assertEqual(result["compat_score"], 45)
        self.assertEqual(result["dimension_score"], 20)
        self.assertEqual(result["risk_components"]["semantic"], 20)
        self.assertEqual(result["score"], 20)
        self.assertEqual(result["semantic_source"], "risk_events")
        decision = await plugin.get_decision_profile("11111", Event({}))
        self.assertEqual(decision["semantic_source"], "risk_events")
        self.assertEqual(decision["semantic_status"], "success")
        self.assertGreater(decision["semantic_time"], 0)

    async def test_fused_canonical_and_legacy_fallback_count_each_scene_once(self):
        plugin = await self.prepared(llm_tags=False, cross_scene_link_enabled=True, risk_semantic_cap=100)
        group_tags = [{"tag": "scam_suspect", "confidence": 1}]
        private_tags = [{"tag": "nsfw_tendency", "confidence": 1, "severity": "clear"}]
        self.install_slot(plugin, "group", group_tags)
        self.install_slot(plugin, "private", private_tags)
        self.install_slot(plugin, "fused", [])
        self.install_canonical(plugin)
        plugin._quotes["11111"] = []
        result = await plugin.get_profile_tags_with_score("11111", Event({}, group=""))
        self.assertEqual(result["scene"], "fused")
        self.assertEqual(result["compat_score"], 85)
        self.assertTrue({"scam_suspect", "nsfw_tendency"}.issubset({item["tag"] for item in result["tags"]}))
        self.assertEqual(result["dimension_score"], 20)
        self.assertEqual(result["risk_components"]["semantic"], 60)
        self.assertEqual(result["score"], 60)
        self.assertEqual(result["semantic_source"], "mixed")
        plugin._tags_cache["11111"]["risk_evidence"]["private"] = {
            "policy": list(policy(plugin)), "events": [],
        }
        migrated = await plugin.get_profile_tags_with_score("11111", Event({}, group=""))
        self.assertEqual(migrated["score"], 20)
        self.assertEqual(migrated["semantic_source"], "risk_events")

    async def test_political_tag_only_scores_in_unmigrated_scene(self):
        plugin = await self.prepared(risk_semantic_cap=100)
        plugin._quotes["11111"] = []
        self.install_canonical(plugin)
        evidence = plugin._tags_cache["11111"].pop("risk_evidence")
        refs = evidence["group"]["events"][0]["evidence_refs"]
        tags = [{"tag": "political_sensitive", "confidence": 1, "evidence_refs": refs}]
        self.install_slot(plugin, "group", tags)
        legacy = await plugin.get_profile_tags_with_score("11111", Event({}))
        self.assertEqual(legacy["risk_components"]["semantic"], 10)
        self.assertEqual(legacy["semantic_source"], "legacy_tags")
        plugin._tags_cache["11111"]["risk_evidence"] = evidence
        canonical = await plugin.get_profile_tags_with_score("11111", Event({}))
        self.assertEqual(canonical["compat_score"], 10)
        self.assertEqual(canonical["dimension_score"], 20)
        self.assertEqual(canonical["risk_components"]["semantic"], 20)
        self.assertEqual(canonical["score"], 20)
        self.assertEqual(canonical["semantic_source"], "risk_events")
        ledger = plugin._tags_cache["11111"]["risk_evidence"]["group"]["events"]
        self.assertEqual(ledger[0]["evidence_refs"], refs)

    async def test_changed_material_throttles_as_stale_and_restoring_material_clears_pending(self):
        plugin = await self.prepared(llm_tag_cache_ttl=3600, llm_refresh_interval=3600)
        plugin._tag_engine.generate_llm_tags = AsyncMock(return_value={
            "tags": [], "traits": [], "impression": "成功印象", "risk_events": [],
        })
        event = Event({})
        first = await plugin.get_profile_tags_with_score("11111", event)
        entry = plugin._tags_cache["11111"]["slots"]["group"]
        original_fingerprint = entry["fingerprint"]
        plugin._quotes["11111"].append(normalize_quote({
            "src": "群 22222", "text": "新材料待分析", "t": int(time.time()),
        }))
        second = await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual(second["score"], first["score"])
        self.assertEqual(second["risk_status"], "stale")
        self.assertTrue(second["review_pending"])
        self.assertNotEqual(entry["pending_fingerprint"], original_fingerprint)
        self.assertEqual(entry["fingerprint"], original_fingerprint)
        plugin._tag_engine.generate_llm_tags.assert_awaited_once()
        plugin._quotes["11111"].pop()
        restored = await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual(restored["risk_status"], "ready")
        self.assertFalse(restored["review_pending"])
        self.assertNotIn("pending_fingerprint", entry)
        plugin._tag_engine.generate_llm_tags.assert_awaited_once()

    async def test_successful_cache_ttl_marks_stale_but_zero_ttl_does_not_age_it(self):
        plugin = await self.prepared(llm_tags=False, llm_tag_cache_ttl=60)
        plugin._quotes["11111"] = []
        entry = self.install_slot(plugin, "group", [{"tag": "friendly", "confidence": 1}])
        entry["time"] = int(time.time()) - 120
        expired = await plugin.get_profile_tags_with_score("11111", Event({}))
        self.assertEqual((expired["score"], expired["risk_status"]), (0, "stale"))
        self.assertTrue(expired["review_pending"])
        plugin.config["llm_tag_cache_ttl"] = 0
        unlimited = await plugin.get_profile_tags_with_score("11111", Event({}))
        self.assertEqual((unlimited["score"], unlimited["risk_status"]), (0, "ready"))
        self.assertFalse(unlimited["review_pending"])

    async def test_untrusted_slot_times_are_safe_for_same_and_changed_fingerprints(self):
        now = int(time.time())
        for stamp in (str(now), "bad", now + 10 * 365 * 86400):
            for changed in (False, True):
                with self.subTest(stamp=stamp, changed=changed):
                    plugin = await self.prepared(llm_tag_cache_ttl=3600, llm_refresh_interval=3600)
                    plugin._tag_engine.generate_llm_tags = AsyncMock(return_value={
                        "tags": [], "traits": [], "impression": "安全刷新", "risk_events": [],
                    })
                    await plugin.get_profile_tags_with_score("11111", Event({}))
                    entry = plugin._tags_cache["11111"]["slots"]["group"]
                    entry["time"] = stamp
                    before = plugin._risk_assessment("11111", [])
                    self.assertIs(type(before["semantic_time"]), int)
                    self.assertGreaterEqual(before["semantic_time"], 0)
                    if stamp != str(now):
                        self.assertEqual(before["semantic_time"], 0)
                        self.assertEqual(before["risk_status"], "stale")
                    if changed:
                        plugin._quotes["11111"].append(normalize_quote({
                            "src": "群 22222", "text": "时间损坏后新材料", "t": now,
                        }))
                    result = await plugin.get_profile_tags_with_score("11111", Event({}))
                    self.assertIs(type(result["semantic_time"]), int)
                    self.assertGreaterEqual(result["semantic_time"], 0)
                    if stamp != str(now):
                        self.assertEqual(plugin._tag_engine.generate_llm_tags.await_count, 2)
                        self.assertEqual(result["risk_status"], "ready")
                    else:
                        plugin._tag_engine.generate_llm_tags.assert_awaited_once()
                        self.assertEqual(result["risk_status"], "stale" if changed else "ready")
                        self.assertEqual(result["review_pending"], changed)
                    decision = await plugin.get_decision_profile("11111", Event({}))
                    self.assertIs(type(decision["semantic_time"]), int)
                    self.assertGreaterEqual(decision["semantic_time"], 0)

