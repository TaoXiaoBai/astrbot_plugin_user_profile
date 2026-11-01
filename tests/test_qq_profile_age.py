import json
import types
import unittest
from unittest.mock import AsyncMock

from tests import test_age_protection as fixtures
from age_protection import profile_age_material


class QQProfileAgeTests(unittest.IsolatedAsyncioTestCase):
    def plugin(self, age=16, **config):
        plugin = fixtures.AgeProtectionTests().make_plugin(history_scan_enabled=False, **config)
        plugin._fetch_stranger_info = AsyncMock(return_value={"user_id": 11111, "age": age})
        async def reply(**kwargs):
            payload = json.loads(kwargs["prompt"].split("\n", 1)[1])
            profiles = [m for m in payload["materials"] if m.get("source") == "qq_profile"]
            evidence = [{"ref": m["ref"], "kind": "qq_profile_age", "supports_minor": m["profile_age"] < 18}
                        for m in profiles]
            return types.SimpleNamespace(completion_text=json.dumps({"status": "ready", "suspected_minor": bool(profiles and profiles[0]["profile_age"] < 18),
                "minor_confidence": .96, "decision_eligible": True, "identity_conflict": False, "evidence": evidence}))
        plugin.context.llm_generate = AsyncMock(side_effect=reply)
        return plugin

    def event(self, text="你好"):
        return fixtures.AgeProtectionTests.event(text)

    async def test_profile_minor_is_high_weight_grounded_evidence(self):
        plugin = self.plugin()
        result = await plugin.assess_age_protection("11111", self.event())
        self.assertTrue(result["decision_eligible"])
        self.assertEqual(result["minor_confidence"], .96)
        self.assertEqual(result["evidence"][0]["kind"], "qq_profile_age")
        payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"].split("\n", 1)[1])
        signal = next(m for m in payload["materials"] if m.get("source") == "qq_profile")
        self.assertEqual(signal["signal_weight"], 85)
        self.assertIn("不是校准概率", plugin.context.llm_generate.await_args.kwargs["prompt"])
        self.assertNotIn("16岁", str(result))

    async def test_profile_and_self_report_conflicts_never_execute(self):
        for age, text in ((16, "我今年20岁"), (20, "我今年16岁")):
            plugin = self.plugin(age)
            result = await plugin.assess_age_protection("11111", self.event(text))
            self.assertTrue(result["identity_conflict"])
            self.assertFalse(result["decision_eligible"])

    async def test_invalid_default_and_other_user_profile_are_ignored(self):
        for age in (0, -1, None, True, 16.5, "unknown", "", 121, "16.0"):
            plugin = self.plugin(age)
            self.assertIsNone(await profile_age_material(plugin, "11111", self.event()))
        plugin = self.plugin()
        plugin._fetch_stranger_info.return_value = {"user_id": "22222", "age": 16}
        self.assertIsNone(await profile_age_material(plugin, "11111", self.event()))
        plugin = self.plugin("17")
        self.assertEqual((await profile_age_material(plugin, "11111", self.event()))["profile_age"], 17)

    async def test_disable_zero_weight_and_failure_degrade(self):
        for config in ({"age_protection_use_qq_age": False}, {"age_protection_qq_age_weight": 0}):
            plugin = self.plugin(**config)
            self.assertIsNone(await profile_age_material(plugin, "11111", self.event()))
            plugin._fetch_stranger_info.assert_not_awaited()
        plugin = self.plugin()
        plugin._fetch_stranger_info.side_effect = RuntimeError("offline")
        result = await plugin.assess_age_protection("11111", self.event())
        self.assertFalse(result["decision_eligible"])

    async def test_cache_refresh_same_age_has_stable_evidence_identity(self):
        plugin = self.plugin()
        first = await profile_age_material(plugin, "11111", self.event())
        second = await profile_age_material(plugin, "11111", self.event())
        self.assertEqual(first, second)
        plugin._fetch_stranger_info.assert_awaited_once()
        for entry in plugin._age_profile_cache.values():
            entry["time"] -= 600
        third = await profile_age_material(plugin, "11111", self.event())
        self.assertEqual(first["ref"], third["ref"])
        self.assertEqual(plugin._fetch_stranger_info.await_count, 2)
        plugin._fetch_stranger_info.return_value = {"user_id": 11111, "age": 18}
        for entry in plugin._age_profile_cache.values():
            entry["time"] -= 600
        adult = await profile_age_material(plugin, "11111", self.event())
        self.assertNotEqual(first["ref"], adult["ref"])

    async def test_model_invented_profile_reference_does_not_count(self):
        plugin = self.plugin()
        plugin.context.llm_generate = AsyncMock(return_value=types.SimpleNamespace(completion_text=json.dumps({
            "status": "ready", "suspected_minor": True, "minor_confidence": .99,
            "decision_eligible": True, "identity_conflict": False,
            "evidence": [{"ref": "invented", "kind": "qq_profile_age", "supports_minor": True}]})))
        result = await plugin.assess_age_protection("11111", self.event())
        self.assertFalse(result["decision_eligible"])
        self.assertFalse(result["evidence"])
