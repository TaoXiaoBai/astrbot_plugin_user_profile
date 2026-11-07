import json
import re
import time
import types
import unittest
from unittest.mock import AsyncMock

import test_plugin
from test_plugin import Event, _new_stat
from profile_core import normalize_quote


class ProfilePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def make_plugin(self):
        plugin = test_plugin.PluginTests().make_plugin({
            "history_scan_enabled": False,
            "friend_reconcile_enabled": False,
            "link_qq_tools_ban": False,
            "llm_provider_id": "mock",
            "llm_tag_cache_ttl": 0,
            "llm_refresh_interval": 0,
            "llm_failure_cache_ttl": 0,
        })
        await plugin._ensure_loaded()
        plugin._stats["11111"] = dict(_new_stat(), g_count=2)
        return plugin

    @staticmethod
    def response(**kwargs):
        refs = re.findall(r"ref=(r_[a-f0-9]{24})", kwargs["prompt"])
        return types.SimpleNamespace(completion_text=json.dumps({
            "tags": [], "impression": "有证据的普通印象", "traits": [],
            "risk_events": [],
            "long_term_traits": [{
                "trait": "偏好分步说明", "confidence": 0.9,
                "reason": "不同日期的材料均支持", "evidence_refs": refs,
            }],
            "retired_long_term_traits": [],
        }))

    async def test_real_engine_long_term_ledger_survives_omission(self):
        plugin = await self.make_plugin()
        now = int(time.time())
        plugin._quotes["11111"] = [
            normalize_quote({"src": "群 22222", "text": "第一天材料", "t": now - 86400}),
            normalize_quote({"src": "群 22222", "text": "第二天材料", "t": now}),
        ]
        plugin.context.llm_generate = AsyncMock(side_effect=self.response)
        event = Event({}, group="22222")
        first = await plugin.get_profile_tags_with_score("11111", event)
        trait = first["long_term_traits"][0]
        self.assertEqual(trait["stability"], "established")
        self.assertEqual(trait["support_count"], 2)
        self.assertEqual(len(trait["evidence_days"]), 2)
        stored = plugin._tags_cache["11111"]["slots"]["group"]
        self.assertNotIn("_evidence_materials", stored)
        self.assertNotIn("第一天材料", json.dumps(stored, ensure_ascii=False))
        self.assertNotIn("第二天材料", json.dumps(stored, ensure_ascii=False))

        plugin._quotes["11111"].append(normalize_quote({
            "src": "群 22222", "text": "后续普通交流", "t": now + 1,
        }))
        plugin.context.llm_generate = AsyncMock(return_value=types.SimpleNamespace(
            completion_text=json.dumps({
                "tags": [], "impression": "近期没有重复该偏好", "traits": [],
                "risk_events": [], "long_term_traits": [],
                "retired_long_term_traits": [],
            })
        ))
        second = await plugin.get_profile_tags_with_score("11111", event)
        retained = second["long_term_traits"][0]
        self.assertEqual(retained["trait"], trait["trait"])
        self.assertEqual(retained["last_confirmed"], trait["last_confirmed"])
        self.assertEqual(retained["support_count"], 2)

    async def test_real_engine_risk_tag_without_event_uses_failure_protection(self):
        plugin = await self.make_plugin()
        now = int(time.time())
        plugin._quotes["11111"] = [
            normalize_quote({"src": "群 22222", "text": "普通材料", "t": now}),
        ]
        plugin.context.llm_generate = AsyncMock(return_value=types.SimpleNamespace(
            completion_text=json.dumps({
                "tags": [], "impression": "最后成功画像", "traits": [],
                "risk_events": [], "long_term_traits": [],
            })
        ))
        event = Event({}, group="22222")
        first = await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual((first["score"], first["risk_status"]), (0, "ready"))

        plugin._quotes["11111"].append(normalize_quote({
            "src": "群 22222", "text": "新材料", "t": now + 1,
        }))
        def invalid_risk(**kwargs):
            ref = re.findall(r"ref=(r_[a-f0-9]{24})", kwargs["prompt"])[-1]
            return types.SimpleNamespace(completion_text=json.dumps({
                "tags": [{
                    "tag": "nsfw_tendency", "confidence": 0.9, "severity": "clear",
                    "reason": "风险摘要", "subject": "self", "evidence_refs": [ref],
                    "support_kind": "observed_behavior",
                }],
                "impression": "不应覆盖", "traits": [], "risk_events": [],
            }))
        plugin.context.llm_generate = AsyncMock(side_effect=invalid_risk)
        degraded = await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual(degraded["impression"], "最后成功画像")
        self.assertEqual((degraded["score"], degraded["risk_status"]), (0, "stale"))
        self.assertNotIn("nsfw_tendency", [item["tag"] for item in degraded["tags"]])
        entry = plugin._tags_cache["11111"]["slots"]["group"]
        self.assertEqual(entry["refresh_status"], "error")
        self.assertNotIn("新材料", json.dumps(entry, ensure_ascii=False))

    async def test_real_engine_retired_only_requires_current_evidence(self):
        plugin = await self.make_plugin()
        plugin._quotes["11111"] = [normalize_quote({
            "src": "群 22222", "text": "本人偏好材料", "t": int(time.time()),
        })]
        plugin.context.llm_generate = AsyncMock(side_effect=self.response)
        event = Event({}, group="22222")
        first = await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual(first["long_term_traits"][0]["trait"], "偏好分步说明")
        original = first["long_term_traits"]
        for refs in ([], ["r_" + "0" * 24]):
            with self.subTest(refs=refs):
                plugin.context.llm_generate = AsyncMock(return_value=types.SimpleNamespace(
                    completion_text=json.dumps({
                        "tags": [], "traits": [], "impression": "缺乏撤回证据", "risk_events": [],
                        "retired_long_term_traits": [{"trait": "偏好分步说明", "evidence_refs": refs}],
                    })
                ))
                retained = await plugin.get_profile_tags_with_score("11111", event)
                self.assertEqual(retained["long_term_traits"], original)
                self.assertEqual(retained["risk_status"], "ready")
                plugin.context.llm_generate.assert_awaited_once()

        def retire(**kwargs):
            refs = re.findall(r"ref=(r_[a-f0-9]{24})", kwargs["prompt"])
            return types.SimpleNamespace(completion_text=json.dumps({
                "tags": [], "traits": [], "impression": "当前材料明确撤回偏好", "risk_events": [],
                "retired_long_term_traits": [{"trait": "偏好分步说明", "evidence_refs": refs}],
            }))
        plugin.context.llm_generate = AsyncMock(side_effect=retire)
        retired = await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual(retired["long_term_traits"], [])
        self.assertEqual(plugin._tags_cache["11111"]["slots"]["group"]["long_term_traits"], [])
        self.assertEqual(retired["risk_status"], "ready")
        plugin.context.llm_generate.assert_awaited_once()

    async def test_real_engine_third_party_event_cannot_support_self_risk_tag(self):
        plugin = await self.make_plugin()
        plugin._quotes["11111"] = [normalize_quote({
            "src": "群 22222", "text": "待分析材料", "t": int(time.time()),
        })]
        plugin.context.llm_generate = AsyncMock(return_value=types.SimpleNamespace(
            completion_text=json.dumps({"tags": [], "traits": [], "impression": "最后成功画像", "risk_events": []})
        ))
        event = Event({}, group="22222")
        first = await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual((first["score"], first["risk_status"]), (0, "ready"))

        def risk_response(subject):
            def respond(**kwargs):
                ref = re.findall(r"ref=(r_[a-f0-9]{24})", kwargs["prompt"])[-1]
                return types.SimpleNamespace(completion_text=json.dumps({
                    "tags": [{"tag": "nsfw_tendency", "confidence": 0.9, "severity": "clear",
                              "reason": "风险摘要", "subject": "self", "evidence_refs": [ref],
                              "support_kind": "observed_behavior"}],
                    "traits": [], "impression": "匹配风险画像",
                    "risk_events": [{"dimension": "content_boundary", "severity": "clear", "confidence": 0.9,
                                     "subject": subject, "evidence_refs": [ref]}],
                }))
            return respond

        plugin.context.llm_generate = AsyncMock(side_effect=risk_response("third_party"))
        degraded = await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual(degraded["impression"], first["impression"])
        self.assertEqual((degraded["score"], degraded["risk_status"]), (0, "stale"))
        self.assertNotIn("nsfw_tendency", [item["tag"] for item in degraded["tags"]])
        self.assertEqual(plugin._tags_cache["11111"]["slots"]["group"]["refresh_status"], "error")
        plugin.context.llm_generate.assert_awaited_once()
        plugin.context.llm_generate = AsyncMock(side_effect=risk_response("self"))
        recovered = await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual((recovered["score"], recovered["risk_status"]), (54, "ready"))
        self.assertIn("nsfw_tendency", [item["tag"] for item in recovered["tags"]])
        self.assertNotIn("refresh_status", plugin._tags_cache["11111"]["slots"]["group"])
        plugin.context.llm_generate.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
