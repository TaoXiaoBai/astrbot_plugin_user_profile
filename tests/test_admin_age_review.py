import asyncio
import copy
import json
import time
import types
import unittest
from unittest.mock import AsyncMock, patch

from age_protection import assess, collect_materials
from tests import test_age_protection as age_fixtures
from tests.test_plugin import Event


class AdminAgeReviewTests(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self, **config):
        return age_fixtures.AgeProtectionTests().make_plugin(history_scan_enabled=False, **config)

    @staticmethod
    def admin(text="/年龄审查 22222", **kwargs):
        event = Event({"message_type": "private", "time": int(time.time())}, group="", text=text, **kwargs)
        event.is_admin = lambda: True
        return event

    @staticmethod
    def quotes(plugin):
        plugin._quotes = {"22222": [{"text": "我今年16岁", "scene": "private", "t": int(time.time())}]}

    async def test_admin_private_other_target_and_no_command_or_admin_quotes(self):
        plugin = self.make_plugin(age_protection_use_qq_age=False)
        self.quotes(plugin)
        plugin._quotes["11111"] = [{"text": "管理员秘密我80岁", "scene": "private", "t": int(time.time())}]
        event = self.admin("/年龄审查 22222 我今年80岁 管理员个人消息")
        event.get_message_str = lambda: self.fail("管理员当前消息不应被读取")
        result = await plugin.review_age_protection("22222", event)
        self.assertTrue(result["decision_eligible"])
        self.assertEqual(result["target_qq"], "22222")
        self.assertEqual(result["assessment_mode"], "admin_review")
        self.assertTrue(result["force_requested"])
        self.assertFalse(result["cache_hit"])
        prompt = plugin.context.llm_generate.call_args.kwargs["prompt"]
        self.assertNotIn("年龄审查", prompt)
        self.assertNotIn("管理员", prompt)
        self.assertNotIn("80岁", prompt)
        self.assertEqual(event.get_sender_id(), "11111")
        denied = await plugin.assess_age_protection("22222", event, force=True)
        self.assertFalse(denied["decision_eligible"])

    async def test_command_alone_never_becomes_target_material(self):
        plugin = self.make_plugin(age_protection_use_qq_age=False)
        result = await plugin.review_age_protection("22222", self.admin("我今年16岁 /年龄审查 22222"))
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["decision_eligible"])
        plugin.context.llm_generate.assert_not_awaited()

    async def test_profile_uses_target_and_rejects_admin_profile_response(self):
        plugin = self.make_plugin()
        event = self.admin()
        event.bot = object()
        plugin._fetch_stranger_info = AsyncMock(return_value={"user_id": "11111", "age": 16})
        denied = await plugin.review_age_protection("22222", event)
        self.assertFalse(denied["decision_eligible"])
        plugin._fetch_stranger_info.assert_awaited_once_with("22222", event)
        plugin.context.llm_generate.assert_not_awaited()
        plugin._age_profile_cache.clear()
        plugin._fetch_stranger_info.return_value = {"user_id": "22222", "age": 16}
        async def response(**kwargs):
            payload = json.loads(kwargs["prompt"].split("\n", 1)[1])
            material = payload["materials"][0]
            self.assertEqual(material["signal_weight"], 85)
            self.assertEqual(material["profile_age"], 16)
            return types.SimpleNamespace(completion_text=json.dumps(dict(
                status="ready", suspected_minor=True, minor_confidence=.96,
                decision_eligible=True, identity_conflict=False,
                evidence=[dict(ref=material["ref"], kind="qq_profile_age", supports_minor=True)])))
        plugin.context.llm_generate.side_effect = response
        result = await plugin.review_age_protection("22222", event)
        self.assertTrue(result["decision_eligible"])
        self.assertEqual(result["evidence"][0]["kind"], "qq_profile_age")

    async def test_authentication_fails_closed_before_loading_or_materials(self):
        cases = []
        normal = self.admin()
        normal.is_admin = lambda: False
        cases.append(normal)
        truthy = self.admin()
        truthy.is_admin = lambda: 1
        cases.append(truthy)
        group = self.admin()
        group._group = "33333"
        cases.append(group)
        for sender in ("", "bad", "123"):
            cases.append(self.admin(sender=sender))
        for method in ("get_group_id", "get_sender_id", "is_admin"):
            broken = self.admin()
            def fail():
                raise RuntimeError("identity unavailable")
            setattr(broken, method, fail)
            cases.append(broken)
        for raw in ({}, {"message_type": "group"}, {"message_type": "unknown"}):
            missing = self.admin()
            missing.message_obj.raw_message = raw
            missing.get_message_type = lambda: "FriendMessage"
            cases.append(missing)
        cases.append(None)
        for event in cases:
            with self.subTest(event=event):
                plugin = self.make_plugin()
                result = await plugin.review_age_protection("22222", event)
                self.assertFalse(result["decision_eligible"])
                self.assertEqual(result["status"], "unknown")
                plugin._ensure_loaded.assert_not_awaited()
                plugin.context.llm_generate.assert_not_awaited()
                self.assertEqual(await collect_materials(plugin, "22222", event, admin_review=True), [])
                direct = await assess(plugin, "22222", event, admin_review=True)
                self.assertFalse(direct["decision_eligible"])

    async def test_invalid_target_rejected(self):
        plugin = self.make_plugin()
        for target in ("", "abc", "123", "1" * 13):
            result = await plugin.review_age_protection(target, self.admin())
            self.assertFalse(result["decision_eligible"])
        plugin._ensure_loaded.assert_not_awaited()

    async def test_mode_fingerprints_caches_and_force_are_isolated(self):
        plugin = self.make_plugin(age_protection_use_qq_age=False)
        self.quotes(plugin)
        automatic = await plugin.assess_age_protection("22222", Event({}, sender="22222", group="", text="我今年16岁"))
        manual = await plugin.review_age_protection("22222", self.admin())
        self.assertNotEqual(automatic["fingerprint"], manual["fingerprint"])
        self.assertEqual(plugin.context.llm_generate.await_count, 2)
        again = await plugin.assess_age_protection("22222", Event({}, sender="22222", group="", text="我今年16岁"))
        self.assertTrue(again["cache_hit"])
        self.assertEqual(again["assessment_mode"], "current_message")
        self.assertEqual(plugin.context.llm_generate.await_count, 2)
        root = plugin._tags_cache["22222"]
        root["age_protection_review_attempt"]["assessed_at"] -= 6
        root["age_protection_review"]["assessed_at"] -= 6
        forced = await plugin.review_age_protection("22222", self.admin())
        self.assertFalse(forced["cache_hit"])
        self.assertTrue(forced["force_requested"])
        self.assertEqual(plugin.context.llm_generate.await_count, 3)
        self.assertEqual(root["age_protection"], plugin._tags_cache["22222"]["age_protection"])

    async def test_ttl_reuse_metadata_keeps_real_timestamp_and_fingerprint(self):
        plugin = self.make_plugin(age_protection_use_qq_age=False)
        self.quotes(plugin)
        first = await plugin.review_age_protection("22222", self.admin(), force=True)
        original = copy.deepcopy(plugin._tags_cache["22222"]["age_protection_review_attempt"])
        with patch("age_protection.time.time", return_value=first["assessed_at"] + 20):
            cached = await plugin.review_age_protection("22222", self.admin("不同管理员命令"), force=False)
        self.assertTrue(cached["cache_hit"])
        self.assertFalse(cached["force_requested"])
        for key in ("assessed_at", "fingerprint", "evidence", "minor_confidence"):
            self.assertEqual(cached[key], first[key])
        self.assertEqual(cached["assessment_mode"], "admin_review")
        self.assertEqual(cached["target_qq"], "22222")
        self.assertEqual(original, plugin._tags_cache["22222"]["age_protection_review_attempt"])
        plugin.context.llm_generate.assert_awaited_once()

    async def test_manual_singleflight_and_five_second_short_cache(self):
        plugin = self.make_plugin(age_protection_use_qq_age=False)
        self.quotes(plugin)
        first, second = await asyncio.gather(
            plugin.review_age_protection("22222", self.admin()),
            plugin.review_age_protection("22222", self.admin("另一个命令")))
        self.assertEqual(first, second)
        cached = await plugin.review_age_protection("22222", self.admin())
        self.assertTrue(cached["cache_hit"])
        self.assertEqual(cached["assessed_at"], first["assessed_at"])
        plugin.context.llm_generate.assert_awaited_once()

    async def test_manual_private_switch_and_adult_conflict(self):
        plugin = self.make_plugin(llm_include_private_quotes=False, age_protection_use_qq_age=False)
        self.quotes(plugin)
        result = await plugin.review_age_protection("22222", self.admin())
        self.assertFalse(result["decision_eligible"])
        plugin.context.llm_generate.assert_not_awaited()
        plugin.config["llm_include_private_quotes"] = True
        plugin._quotes["22222"].append({"text": "我已成年了", "scene": "group", "t": int(time.time())})
        result = await plugin.review_age_protection("22222", self.admin())
        self.assertTrue(result["identity_conflict"])
        self.assertFalse(result["decision_eligible"])

    async def test_manual_history_precise_target_not_admin(self):
        plugin = self.make_plugin(age_protection_use_qq_age=False)
        plugin.config["history_scan_enabled"] = True
        conv = lambda owner, text: types.SimpleNamespace(user_id="snowluma:FriendMessage:" + owner,
            history=json.dumps([dict(role="user", content=text, timestamp=int(time.time()))]))
        manager = types.SimpleNamespace(get_filtered_conversations=AsyncMock(return_value=(
            [conv("11111", "管理员秘密我80岁"), conv("22222", "我今年16岁")], 2)))
        plugin.context.conversation_manager = manager
        result = await plugin.review_age_protection("22222", self.admin())
        self.assertTrue(result["decision_eligible"])
        self.assertEqual(manager.get_filtered_conversations.call_args.kwargs["search_query"], "22222")
        self.assertNotIn("管理员秘密", plugin.context.llm_generate.call_args.kwargs["prompt"])

    async def test_manual_deletion_generation_and_timeout_never_success(self):
        for delete in (True, False):
            plugin = self.make_plugin(age_protection_use_qq_age=False)
            self.quotes(plugin)
            entered, release = asyncio.Event(), asyncio.Event()
            async def delayed(**kwargs):
                entered.set()
                await release.wait()
                return await age_fixtures.AgeProtectionTests.reply(**kwargs)
            plugin.context.llm_generate.side_effect = delayed
            if not delete:
                plugin.config["age_protection_timeout_seconds"] = .02
            task = asyncio.create_task(plugin.review_age_protection("22222", self.admin()))
            await entered.wait()
            if delete:
                plugin._user_generations["22222"] = 1
                plugin._tags_cache.pop("22222", None)
                release.set()
            result = await task
            self.assertFalse(result["decision_eligible"])
            self.assertEqual(result["status"], "unknown" if delete else "error")
            self.assertNotIn("age_protection_review", plugin._tags_cache.get("22222", {}))

    async def test_review_cache_fields_hidden_from_public(self):
        plugin = self.make_plugin()
        result = plugin._public_profile_view({"age_protection_review": {"evidence": [1]},
            "age_protection_review_attempt": {"minor_confidence": .99}}, Event({}))
        self.assertNotIn("age_protection_review", result)
        self.assertNotIn("age_protection_review_attempt", result)
