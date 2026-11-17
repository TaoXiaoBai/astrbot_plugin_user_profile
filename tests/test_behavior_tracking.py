import json
import re
import time
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_plugin
from test_plugin import Event
from behavior_tracking import day_number, memory_material, record_message, summarize
from trait_evidence import review_long_term_traits


class BehaviorTrackingTests(unittest.IsolatedAsyncioTestCase):
    make_plugin = test_plugin.PluginTests.make_plugin

    def test_daily_aggregation_streak_trend_and_retention(self):
        now = int(time.time())
        stats = {}
        record_message(
            stats, now - 100 * 86400, group=True, images=0, links=0,
            mentions=0, chars=1, night=True, retention_days=90,
        )
        for offset, group in ((31, True), (30, False), (2, True), (1, True), (0, False)):
            record_message(
                stats, now - offset * 86400, group=group, images=1, links=0,
                mentions=0, chars=20, night=False, retention_days=90,
            )
        self.assertNotIn(str(day_number(now - 100 * 86400)), stats["behavior_days"])
        summary = summarize(stats, now)
        self.assertEqual(summary["messages"], 3)
        self.assertEqual(summary["active_days"], 3)
        self.assertEqual(summary["streak"], 3)
        self.assertEqual(summary["previous_messages"], 2)
        self.assertIn("近30天群私合计活跃3天、发言3条", summary["text"])
        self.assertIn("较前30天+50%", summary["text"])
        group = summarize(stats, now, "group")
        private = summarize(stats, now, "private")
        self.assertEqual(group["messages"], 2)
        self.assertEqual(private["messages"], 1)
        material = memory_material(stats, now, "group")
        self.assertEqual(material["evidence_kind"], "behavior")
        self.assertIn("非用户原话", material["text"])

    def test_out_of_order_old_record_never_evicts_newer_days(self):
        now = int(time.time())
        stats = {}
        record_message(stats, now, group=True, images=0, links=0, mentions=0,
                       chars=1, night=False, retention_days=90)
        record_message(stats, now - 100 * 86400, group=True, images=0, links=0,
                       mentions=0, chars=1, night=False, retention_days=90)
        self.assertIn(str(day_number(now)), stats["behavior_days"])
        self.assertNotIn(str(day_number(now - 100 * 86400)), stats["behavior_days"])

    async def test_message_collection_updates_bounded_behavior_and_activity(self):
        plugin = self.make_plugin({
            "history_scan_enabled": False, "friend_reconcile_enabled": False,
            "link_qq_tools_ban": False, "behavior_history_days": 7,
        })
        now = int(time.time())
        raw = {
            "post_type": "message", "message_type": "group", "user_id": "11111",
            "self_id": "99999", "group_id": "22222",
        }
        with patch("main.time.time", return_value=now):
            await plugin.on_message(Event(raw, sender="11111", group="22222", text="https://example.com @test"))
        stat = plugin._stats["11111"]
        self.assertEqual(len(stat["behavior_days"]), 1)
        day = next(iter(stat["behavior_days"].values()))
        self.assertEqual((day["g"], day["p"], day["links"], day["mentions"]), (1, 0, 1, 1))
        self.assertIn("行为轨迹", plugin._format_activity(stat))

    async def test_behavior_only_memory_is_allowed_but_cannot_create_risk_evidence(self):
        plugin = self.make_plugin({
            "history_scan_enabled": False, "friend_reconcile_enabled": False,
            "link_qq_tools_ban": False, "store_quotes": False,
            "llm_provider_id": "mock", "llm_refresh_interval": 0,
            "llm_tag_cache_ttl": 0, "llm_failure_cache_ttl": 0,
        })
        raw = {
            "post_type": "message", "message_type": "group", "user_id": "11111",
            "self_id": "99999", "group_id": "22222",
        }
        await plugin.on_message(Event(raw, sender="11111", group="22222", text="不保存的原话"))

        async def respond(**kwargs):
            prompt = kwargs["prompt"]
            match = re.search(r"ref=(r_[a-f0-9]{24})[^]]*证据类型=behavior_summary", prompt)
            self.assertIsNotNone(match, prompt)
            ref = match.group(1)
            return types.SimpleNamespace(completion_text=json.dumps({
                "tags": [], "impression": "近期有持续行为记录", "traits": [],
                "risk_events": [{
                    "dimension": "content_boundary", "severity": "severe", "confidence": 0.9,
                    "subject": "self", "evidence_refs": [ref], "observed_at": int(time.time()),
                }],
                "long_term_traits": [{
                    "trait": "近期持续活跃", "memory_kind": "behavior", "confidence": 0.9,
                    "reason": "按日行为统计支持", "evidence_refs": [ref],
                }],
                "retired_long_term_traits": [],
            }))

        plugin.context.llm_generate = AsyncMock(side_effect=respond)
        result = await plugin.get_profile_tags_with_score("11111", Event({}, group="22222"))
        self.assertEqual(result["risk_events"], [])
        self.assertEqual(result["tags"][0]["tag"], "active_low")
        trait = result["long_term_traits"][0]
        self.assertEqual(trait["trait"], "近期持续活跃")
        self.assertEqual(trait["memory_kind"], "behavior")
        self.assertEqual(trait["support_count"], 1)

    async def test_behavior_summary_only_supports_behavior_memory_and_confidence_gate(self):
        now = int(time.time())
        stats = {}
        record_message(
            stats, now, group=True, images=0, links=0, mentions=0,
            chars=10, night=False, retention_days=90,
        )
        material = memory_material(stats, now, "group")
        materials = {"r_behavior": material}
        for kind, confidence, expected in (
            ("behavior", 0.8, 1), ("preference", 0.9, 0),
            ("behavior", 0.7, 0), ("interest", 0.95, 0),
        ):
            with self.subTest(kind=kind, confidence=confidence):
                result = review_long_term_traits([], [{
                    "trait": "测试记忆", "memory_kind": kind, "confidence": confidence,
                    "evidence_refs": ["r_behavior"],
                }], [], materials, now, min_confidence=0.75)
                self.assertEqual(len(result), expected)


if __name__ == "__main__":
    unittest.main()
