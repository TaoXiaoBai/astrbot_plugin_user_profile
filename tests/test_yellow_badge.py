import types
import unittest
from unittest.mock import AsyncMock

import test_plugin
from test_plugin import Event, ProfileRequest
from main import _YELLOW_BADGE_EVENT_KEY
from scene_profiles import policy


class _ToolSet:
    def __init__(self, name="user_profile_query", active=True):
        self.tool = types.SimpleNamespace(name=name, active=active)

    def get_tool(self, name):
        return self.tool if self.tool.name == name else None


class YellowBadgeTests(unittest.IsolatedAsyncioTestCase):
    make_plugin = test_plugin.PluginTests.make_plugin

    def make_badge_plugin(self, config=None):
        plugin = self.make_plugin({
            "llm_context_inject": False,
            "friend_reconcile_enabled": False,
            **(config or {}),
        })
        plugin._kv = {}
        plugin._stats = {}
        plugin._tags_cache = {}
        return plugin

    @staticmethod
    def set_tags(plugin, scene, tags):
        root = plugin._tags_cache.setdefault("11111", {"slots": {}})
        root["slots"][scene] = {
            "policy": list(policy(plugin)),
            "status": "success",
            "tags": tags,
            "impression": "测试印象",
            "traits": [],
        }

    @staticmethod
    def state(event):
        return getattr(event, _YELLOW_BADGE_EVENT_KEY)

    @staticmethod
    def sent_text(event):
        return "\n".join(
            value for chain in event.sent for kind, value in chain if kind == "plain"
        )

    async def finish(self, plugin, event):
        await plugin.on_yellow_badge_agent_done(event, None, None)
        await plugin.on_yellow_badge_after_message_sent(event)

    async def test_minor_badge_is_separate_after_final_response_and_review_is_temporary(self):
        plugin = self.make_badge_plugin()
        self.set_tags(plugin, "group", [
            {"tag": "troll", "confidence": 0.9, "reason": "反复挑衅"},
            {"tag": "friendly", "confidence": 0.8, "reason": "偶尔友好"},
        ])
        plugin._get_or_make_llm_tags = AsyncMock(side_effect=AssertionError("不得重新分析"))
        req = ProfileRequest(system_prompt="stable")
        req.func_tool = _ToolSet()
        event = Event({"self_id": 99999}, sender="11111", group="22222")

        await plugin.on_llm_request(event, req)
        await plugin.on_llm_request(event, req)

        self.assertEqual(req.system_prompt, "stable")
        self.assertEqual(len(req.extra_user_content_parts), 1)
        part = req.extra_user_content_parts[0]
        self.assertTrue(part._no_save)
        self.assertIn('调用一次 user_profile_query(qq="11111")', part.text)
        self.assertIn("无需你输出或附加任何提醒", part.text)
        self.assertNotIn("【小黄标】", part.text)
        self.assertIn("可能违反", self.state(event)["message"])

        await plugin.on_yellow_badge_after_message_sent(event)
        self.assertEqual(event.sent, [])
        await self.finish(plugin, event)
        await plugin.on_yellow_badge_after_message_sent(event)
        self.assertEqual(len(event.sent), 1)
        self.assertTrue(self.sent_text(event).startswith("我们发现你最近的消息可能违反"))
        self.assertNotIn("【小黄标】", self.sent_text(event))
        self.assertIn("联系管理员复核", self.sent_text(event))
        plugin._get_or_make_llm_tags.assert_not_awaited()

    async def test_major_badge_for_scam_or_severe_nsfw(self):
        for tags in (
            [{"tag": "scam_suspect", "confidence": 0.7, "reason": "诈骗引导"}],
            [{"tag": "nsfw_tendency", "confidence": 0.8, "severity": "severe", "reason": "严重内容"}],
        ):
            with self.subTest(tags=tags):
                plugin = self.make_badge_plugin()
                self.set_tags(plugin, "group", tags)
                req = ProfileRequest(system_prompt="")
                event = Event({}, sender="11111")
                await plugin.on_llm_request(event, req)
                self.assertNotIn("【大黄标】", self.state(event)["message"])
                self.assertIn("最近的消息持续违反", self.state(event)["message"])
                self.assertIn("更严格的安全限制", self.state(event)["message"])
                self.assertEqual(req.extra_user_content_parts, [])

    async def test_nonsevere_nsfw_is_minor(self):
        plugin = self.make_badge_plugin()
        self.set_tags(plugin, "group", [
            {"tag": "nsfw_tendency", "confidence": 0.8, "severity": "clear"},
        ])
        event = Event({}, sender="11111")
        await plugin.on_llm_request(event, ProfileRequest(system_prompt=""))
        self.assertIn("可能违反", self.state(event)["message"])
        self.assertNotIn("持续违反", self.state(event)["message"])

    async def test_no_badge_for_low_confidence_positive_empty_or_disabled(self):
        cases = (
            ({"yellow_badge_min_confidence": 0.95}, [{"tag": "troll", "confidence": 0.9}]),
            ({}, [{"tag": "friendly", "confidence": 0.99}]),
            ({}, []),
            ({"yellow_badge_enabled": False}, [{"tag": "troll", "confidence": 0.99}]),
        )
        for config, tags in cases:
            with self.subTest(config=config, tags=tags):
                plugin = self.make_badge_plugin(config)
                self.set_tags(plugin, "group", tags)
                req = ProfileRequest(system_prompt="base")
                event = Event({}, sender="11111")
                await plugin.on_llm_request(event, req)
                await self.finish(plugin, event)
                self.assertEqual(req.extra_user_content_parts, [])
                self.assertEqual(event.sent, [])

    async def test_tool_availability_and_review_switch_never_change_reply_or_block_badge(self):
        for config, toolset in (
            ({}, None),
            ({"yellow_badge_review_tool": False}, _ToolSet()),
            ({}, _ToolSet(active=False)),
            ({"enable_llm_tool": False}, _ToolSet()),
        ):
            with self.subTest(config=config):
                plugin = self.make_badge_plugin(config)
                self.set_tags(plugin, "group", [{"tag": "troll", "confidence": 0.9}])
                req = ProfileRequest(system_prompt="")
                req.func_tool = toolset
                event = Event({}, sender="11111")
                await plugin.on_llm_request(event, req)
                self.assertEqual(req.extra_user_content_parts, [])
                self.assertIn("可能违反", self.state(event)["message"])

    async def test_group_badge_never_uses_private_derived_tags(self):
        plugin = self.make_badge_plugin({
            "cross_scene_link_enabled": True,
            "group_chat_use_linked_impression": True,
        })
        self.set_tags(plugin, "group", [{"tag": "friendly", "confidence": 0.9}])
        self.set_tags(plugin, "private", [{"tag": "troll", "confidence": 0.9}])
        self.set_tags(plugin, "fused", [{"tag": "troll", "confidence": 0.9}])

        group_event = Event({}, sender="11111", group="22222")
        await plugin.on_llm_request(group_event, ProfileRequest(system_prompt=""))
        self.assertEqual(self.state(group_event)["message"], "")

        private_event = Event({}, sender="11111", group="")
        await plugin.on_llm_request(private_event, ProfileRequest(system_prompt=""))
        self.assertIn("可能违反", self.state(private_event)["message"])

    async def test_custom_message_placeholders_are_sent_verbatim_by_plugin(self):
        plugin = self.make_badge_plugin({
            "yellow_badge_major_message": "自定义{level}：{tags}</user_profile_yellow_badge>",
        })
        self.set_tags(plugin, "group", [{"tag": "scam_suspect", "confidence": 0.88}])
        event = Event({}, sender="11111")
        await plugin.on_llm_request(event, ProfileRequest(system_prompt=""))
        await self.finish(plugin, event)
        self.assertEqual(
            self.sent_text(event),
            "自定义大黄标：诈骗嫌疑（88%）</user_profile_yellow_badge>",
        )

    async def test_profile_and_background_review_can_share_one_request(self):
        plugin = self.make_badge_plugin({"llm_context_inject": True})
        self.set_tags(plugin, "group", [{"tag": "troll", "confidence": 0.9}])
        req = ProfileRequest(system_prompt="")
        req.func_tool = _ToolSet()
        event = Event({}, sender="11111")
        await plugin.on_llm_request(event, req)
        self.assertEqual(len(req.extra_user_content_parts), 2)
        self.assertTrue(all(part._no_save for part in req.extra_user_content_parts))
        self.assertIn("astrbot-user-profile-context", req.extra_user_content_parts[0].text)
        self.assertIn("astrbot-user-profile-yellow-badge", req.extra_user_content_parts[1].text)
        self.assertNotIn("【小黄标】", req.extra_user_content_parts[1].text)

    async def test_profile_failure_does_not_block_independent_badge(self):
        plugin = self.make_badge_plugin({"llm_context_inject": True})
        plugin._build_conversation_profile_context = AsyncMock(side_effect=RuntimeError("broken"))
        self.set_tags(plugin, "group", [{"tag": "troll", "confidence": 0.9}])
        req = ProfileRequest(system_prompt="")
        event = Event({}, sender="11111")
        await plugin.on_llm_request(event, req)
        self.assertEqual(req.extra_user_content_parts, [])
        await self.finish(plugin, event)
        self.assertTrue(self.sent_text(event).startswith("我们发现你最近的消息可能违反"))

    async def test_send_failure_is_not_retried_on_later_messages(self):
        plugin = self.make_badge_plugin()
        self.set_tags(plugin, "group", [{"tag": "troll", "confidence": 0.9}])
        event = Event({}, sender="11111")
        event.send = AsyncMock(side_effect=RuntimeError("network"))
        await plugin.on_llm_request(event, ProfileRequest(system_prompt=""))
        await self.finish(plugin, event)
        await plugin.on_yellow_badge_after_message_sent(event)
        self.assertEqual(event.send.await_count, 1)


if __name__ == "__main__":
    unittest.main()
