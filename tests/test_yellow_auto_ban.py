import copy
import time
import types
import unittest
from unittest.mock import AsyncMock

import test_plugin
from test_plugin import Event, ProfileRequest
from scene_profiles import policy


class YellowAutoBanTests(unittest.IsolatedAsyncioTestCase):
    make_plugin = test_plugin.PluginTests.make_plugin

    def setUp(self):
        self.now = int(time.time())
        self.plugin = self.make_plugin({
            "llm_context_inject": False,
            "friend_reconcile_enabled": False,
            "yellow_badge_auto_ban_enabled": True,
            "yellow_badge_auto_ban_min_events": 3,
            "yellow_badge_auto_ban_sustain_seconds": 86400,
            "yellow_badge_auto_ban_window_days": 7,
            "yellow_badge_auto_ban_duration_seconds": 86400,
        })
        self.plugin._stats = {}
        self.plugin._tags_cache = {}
        self.tools = types.SimpleNamespace(
            config={"ban_list": []},
            set_temporary_safety_ban=AsyncMock(return_value={"status": "success", "created": True}),
        )
        self.plugin.context = types.SimpleNamespace(
            get_registered_star=lambda name: types.SimpleNamespace(star_cls=self.tools)
        )
        self.event = Event({}, sender="11111", group="22222")
        self.set_profile()

    def set_profile(self, events=None, tags=None, scene="group"):
        events = self.events() if events is None else events
        tags = [{"tag": "nsfw_tendency", "confidence": 0.95, "severity": "severe"}] if tags is None else tags
        root = self.plugin._tags_cache.setdefault("11111", {"slots": {}})
        root["slots"][scene] = {
            "policy": list(policy(self.plugin)), "status": "success",
            "tags": tags, "risk_events": events, "impression": "", "traits": [],
        }
        root.setdefault("risk_evidence", {})[scene] = {
            "policy": list(policy(self.plugin)), "events": copy.deepcopy(events),
        }

    def events(self, count=3, *, severity="severe", dimension="content_boundary", start=None):
        start = self.now - 3 * 86400 if start is None else start
        result = []
        for index in range(count):
            ref = f"r_{index + 1:024d}"
            result.append({
                "episode_id": ref, "dimension": dimension, "severity": severity,
                "confidence": 0.95, "subject": "self", "evidence_refs": [ref],
                "observed_at": start + index * 3600,
            })
        return result

    async def test_eligible_evidence_calls_qq_tools_without_user_notification(self):
        req = ProfileRequest(system_prompt="")
        await self.plugin.on_llm_request(self.event, req)
        self.tools.set_temporary_safety_ban.assert_awaited_once()
        kwargs = self.tools.set_temporary_safety_ban.await_args.kwargs
        self.assertEqual(kwargs["duration"], 86400)
        self.assertEqual(kwargs["source"], "yellow_badge")
        self.assertTrue(kwargs["event_key"].startswith("yellow:"))
        self.assertEqual(self.event.sent, [])
        self.assertEqual(req.extra_user_content_parts, [])
        await self.plugin.on_yellow_badge_agent_done(self.event, None, None)
        await self.plugin.on_yellow_badge_after_message_sent(self.event)
        reminder = self.event.sent[0][0][1]
        self.assertIn("最近的消息持续违反", reminder)
        self.assertNotIn("【大黄标】", reminder)
        self.assertNotIn("拉黑", reminder)
        self.assertNotIn("黑名单", reminder)

    async def test_disabled_switch_never_looks_up_qq_tools(self):
        self.plugin.config["yellow_badge_auto_ban_enabled"] = False
        self.plugin.context = types.SimpleNamespace(
            get_registered_star=lambda name: (_ for _ in ()).throw(AssertionError("不得读取"))
        )
        result = await self.plugin._maybe_auto_ban_yellow_badge("11111", self.event)
        self.assertEqual(result["status"], "disabled")

    async def test_count_duration_severity_and_major_tag_are_all_required(self):
        cases = (
            (self.events(2), None, "insufficient_events"),
            (self.events(3, start=self.now - 3 * 3600), None, "not_sustained"),
            (self.events(3, severity="clear"), None, "insufficient_events"),
            (self.events(3), [{"tag": "troll", "confidence": 0.99}], "insufficient_events"),
        )
        for events, tags, expected in cases:
            with self.subTest(expected=expected):
                self.plugin._tags_cache = {}
                self.set_profile(events=events, tags=tags)
                self.tools.set_temporary_safety_ban.reset_mock()
                result = await self.plugin._maybe_auto_ban_yellow_badge("11111", self.event)
                self.assertEqual(result["status"], expected)
                self.tools.set_temporary_safety_ban.assert_not_awaited()

    async def test_same_episode_across_dimensions_counts_once(self):
        events = self.events(2)
        second = dict(events[0], dimension="fraud", id="fraud:" + events[0]["episode_id"])
        self.plugin._tags_cache = {}
        self.set_profile(events=events + [second])
        result = await self.plugin._maybe_auto_ban_yellow_badge("11111", self.event)
        self.assertEqual(result["status"], "insufficient_events")
        self.tools.set_temporary_safety_ban.assert_not_awaited()

    async def test_active_owned_ban_prevents_duplicate_writes(self):
        self.tools.config["ban_list"] = [{
            "user_id": "11111", "ban_time": self.now - 100,
            "duration": 1000, "temporary_safety_ban": True, "source": "yellow_badge",
        }]
        result = await self.plugin._maybe_auto_ban_yellow_badge("11111", self.event)
        self.assertEqual(result["status"], "already_active")
        self.tools.set_temporary_safety_ban.assert_not_awaited()

    async def test_expired_ban_requires_a_new_violation(self):
        self.tools.config["ban_list"] = [{
            "user_id": "11111", "ban_time": self.now - 10000,
            "duration": 1, "temporary_safety_ban": True, "source": "yellow_badge",
        }]
        result = await self.plugin._maybe_auto_ban_yellow_badge("11111", self.event)
        self.assertEqual(result["status"], "no_new_violation")
        self.tools.set_temporary_safety_ban.assert_not_awaited()

        new_event = self.events(1, start=self.now - 100)[0]
        new_event["episode_id"] = new_event["evidence_refs"][0] = "r_999999999999999999999999"
        root = self.plugin._tags_cache["11111"]
        root["slots"]["group"]["risk_events"].append(new_event)
        result = await self.plugin._maybe_auto_ban_yellow_badge("11111", self.event)
        self.assertEqual(result["status"], "success")
        self.tools.set_temporary_safety_ban.assert_awaited_once()

    async def test_admin_is_exempt(self):
        self.event.is_admin = lambda: True
        result = await self.plugin._maybe_auto_ban_yellow_badge("11111", self.event)
        self.assertEqual(result["status"], "admin_exempt")
        self.tools.set_temporary_safety_ban.assert_not_awaited()

    async def test_qq_tools_failure_fails_closed_without_chat_output(self):
        self.tools.set_temporary_safety_ban = AsyncMock(side_effect=RuntimeError("save failed"))
        req = ProfileRequest(system_prompt="")
        await self.plugin.on_llm_request(self.event, req)
        self.assertEqual(self.event.sent, [])
        self.assertEqual(req.extra_user_content_parts, [])

    async def test_group_enforcement_does_not_consume_private_only_evidence(self):
        self.plugin.config.update({
            "cross_scene_link_enabled": True,
            "group_chat_use_linked_impression": True,
        })
        self.plugin._tags_cache = {}
        self.set_profile(events=[], tags=[{"tag": "friendly", "confidence": 0.9}], scene="group")
        self.set_profile(scene="private")
        self.set_profile(scene="fused")
        result = await self.plugin._maybe_auto_ban_yellow_badge("11111", self.event)
        self.assertEqual(result["status"], "insufficient_events")
        self.tools.set_temporary_safety_ban.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
