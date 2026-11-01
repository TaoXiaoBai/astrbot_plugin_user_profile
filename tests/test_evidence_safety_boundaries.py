import time
import unittest

from tests import test_age_protection as age_fixtures
from risk_dimensions import material_refs, sanitize_events, merge_events, score_dimensions


class EvidenceBoundaryTests(unittest.TestCase):
    def test_refresh_keeps_connecting_refs_and_earliest_time(self):
        now = 1800000000
        materials = material_refs("11111", [{"text": text, "t": now - index * 3600, "scene": "private"}
                                           for index, text in enumerate(("a", "b", "c"))])
        a, b, c = list(materials)
        def event(episode, refs, confidence):
            return sanitize_events([{"episode_id": episode, "dimension": "content_boundary", "severity": "clear",
                "confidence": confidence, "subject": "self", "evidence_refs": refs}], materials)
        previous = event(a, [a, b], .8) + event(b, [b], .7)
        updated = merge_events(previous, event(a, [a, c], 1), now)
        root = next(item for item in updated if item["episode_id"] == a)
        self.assertEqual(set(root["evidence_refs"]), {a, b, c})
        self.assertEqual(root["observed_at"], now - 7200)
        result = score_dimensions(updated, now, {"risk_semantic_cap": 100})
        self.assertEqual(result["risk_dimensions"]["content_boundary"]["episodes"], 1)

    def test_continuous_story_messages_do_not_increase_persistence(self):
        now = 1800000000
        materials = material_refs("11111", [{"text": str(i), "t": now - i * 600, "scene": "private"} for i in range(6)])
        events = []
        for ref in materials:
            events.extend(sanitize_events([{"dimension": "content_boundary", "severity": "clear", "confidence": 1,
                                           "subject": "self", "evidence_refs": [ref]}], materials))
        result = score_dimensions(events, now, {"risk_semantic_cap": 100})
        self.assertEqual(result["risk_dimensions"]["content_boundary"]["episodes"], 1)
        self.assertEqual(result["risk_dimensions"]["content_boundary"]["persistence"], 1)

    def test_connectivity_capacity_disables_extra_accumulation(self):
        now = 1800000000
        materials = material_refs("11111", [{"text": str(i), "t": now - i * 100, "scene": "private"} for i in range(80)])
        refs = list(materials)
        previous = []
        for ref in refs[1:]:
            current = sanitize_events([{"dimension": "content_boundary", "episode_id": refs[0], "severity": "clear",
                "confidence": 1, "subject": "self", "evidence_refs": [refs[0], ref]}], materials)
            previous = merge_events(previous, current, now)
        self.assertTrue(previous[0]["connectivity_limited"])
        self.assertLessEqual(len(previous[0]["evidence_refs"]), 64)
        result = score_dimensions(previous, now, {"risk_semantic_cap": 100})
        self.assertEqual(result["risk_dimensions"]["content_boundary"]["persistence"], 1)


class AgeIncompleteBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_truncated_current_cannot_hide_adult_counterevidence(self):
        fixture = age_fixtures.AgeProtectionTests()
        plugin = fixture.make_plugin(history_scan_enabled=False)
        plugin._quotes["11111"] = [{"src": "私聊", "scene": "private", "t": int(time.time()), "text": "我今年16岁"}]
        segment_cap = min(8000, plugin.config["age_protection_max_chars"])
        result = await plugin.assess_age_protection("11111", fixture.event("闲聊" * (segment_cap // 2 + 1) + "我已经成年了"))
        self.assertFalse(result["decision_eligible"])
        self.assertIn("不可据此自动限制", result["reason"])
