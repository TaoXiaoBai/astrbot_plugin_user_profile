import asyncio
import json
import time
import types
import unittest
from unittest.mock import AsyncMock

from tests.test_plugin import Event
from tests import test_plugin as fixtures
from age_protection import collect_materials, hard_age, sanitize


class AgeProtectionTests(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self, **config):
        plugin = fixtures.PluginTests().make_plugin({"llm_provider_id": "mock", **config})
        plugin._ensure_loaded = AsyncMock()
        plugin._stats, plugin._quotes, plugin._tags_cache = {}, {}, {}
        plugin.context.llm_generate = AsyncMock(side_effect=self.reply)
        return plugin

    @staticmethod
    async def reply(**kwargs):
        payload = json.loads(kwargs["prompt"].split("\n", 1)[1])
        materials = payload["materials"]
        evidence = [{"ref": m["ref"], "kind": "self_report_age", "supports_minor": True,
                     "reason": m["text"]} for m in materials]
        return types.SimpleNamespace(completion_text=json.dumps({
            "status": "ready", "suspected_minor": True, "minor_confidence": .96,
            "decision_eligible": True, "identity_conflict": False,
            "evidence": evidence, "reason": "raw private text"}))

    @staticmethod
    def event(text="我今年16岁", **kwargs):
        return Event({"time": int(time.time())}, group="", text=text, **kwargs)

    async def test_real_api_current_message_without_profile_refresh(self):
        plugin = self.make_plugin(history_scan_enabled=False)
        result = await plugin.assess_age_protection("11111", self.event())
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["decision_eligible"])
        self.assertTrue(result["suspected_minor"])
        self.assertNotIn("16岁", json.dumps(result, ensure_ascii=False))
        self.assertIn("age_protection", plugin._kv["up_tags"]["11111"])
        self.assertNotIn("up_management_events", plugin._kv)

    async def test_wrong_sender_invalid_qq_disabled_never_reads_or_calls(self):
        plugin = self.make_plugin()
        for qq, event in [("22222", self.event()), ("123", self.event()), ("11111", None)]:
            result = await plugin.assess_age_protection(qq, event)
            self.assertFalse(result["decision_eligible"])
        plugin._ensure_loaded.assert_not_awaited()
        plugin.config["age_protection_enabled"] = False
        await plugin.assess_age_protection("11111", self.event())
        plugin.context.llm_generate.assert_not_awaited()

    async def test_singleflight_cache_force_and_new_current_material(self):
        plugin = self.make_plugin(history_scan_enabled=False)
        event = self.event()
        first, second = await asyncio.gather(*(plugin.assess_age_protection("11111", event) for _ in range(2)))
        self.assertEqual(first, second)
        await plugin.assess_age_protection("11111", event, force=True)
        self.assertEqual(plugin.context.llm_generate.await_count, 1)
        await plugin.assess_age_protection("11111", self.event("我今年15岁"))
        self.assertEqual(plugin.context.llm_generate.await_count, 2)

    async def test_private_policy_force_and_group_isolation(self):
        plugin = self.make_plugin(llm_include_private_quotes=False)
        plugin._quotes = {"11111": [{"text": "PRIVATE_SECRET", "src": "私聊", "t": int(time.time())}]}
        manager = types.SimpleNamespace(get_filtered_conversations=AsyncMock())
        plugin.context.conversation_manager = manager
        await plugin.assess_age_protection("11111", self.event(), force=True)
        self.assertNotIn("PRIVATE_SECRET", plugin.context.llm_generate.call_args.kwargs["prompt"])
        manager.get_filtered_conversations.assert_not_awaited()
        plugin.config["llm_include_private_quotes"] = True
        await plugin.assess_age_protection("11111", Event({}, text="我16岁"))
        self.assertNotIn("PRIVATE_SECRET", plugin.context.llm_generate.call_args.kwargs["prompt"])
        manager.get_filtered_conversations.assert_not_awaited()

    async def test_history_exact_owner_role_sender_and_summary(self):
        plugin = self.make_plugin()
        now = int(time.time())
        def conv(origin, history):
            return types.SimpleNamespace(user_id=origin, history=json.dumps(history))
        items = [conv("snowluma:FriendMessage:11111", [
            {"role": "user", "content": "本人消息", "timestamp": now},
            {"role": "assistant", "content": "助手秘密"},
            {"role": "user", "content": "摘要秘密", "is_summary": True},
            {"role": "user", "content": "系统提醒:我16岁"},
            {"role": "user", "content": "他人秘密", "sender_id": "22222"}]),
            conv("snowluma:FriendMessage:22222", [{"role": "user", "content": "检索命中不是归属"}]),
            conv("other:FriendMessage:11111", [{"role": "user", "content": "平台不是归属"}]),
            conv("snowluma:GroupMessage:12345", [{"role": "user", "sender_id": "11111", "content": "确认本人", "timestamp": now}])]
        plugin.context.conversation_manager = types.SimpleNamespace(get_filtered_conversations=AsyncMock(return_value=(items, 4)))
        materials = await collect_materials(plugin, "11111", self.event())
        text = json.dumps(materials, ensure_ascii=False)
        self.assertIn("本人消息", text)
        self.assertIn("确认本人", text)
        for rejected in ("助手秘密", "摘要秘密", "他人秘密", "检索命中不是归属", "平台不是归属", "系统提醒"):
            self.assertNotIn(rejected, text)

    async def test_timeout_failure_preserves_profile_and_age_success(self):
        plugin = self.make_plugin(history_scan_enabled=False)
        event = self.event()
        old = await plugin.assess_age_protection("11111", event)
        plugin.config["age_protection_timeout_seconds"] = .01
        async def slow(**kwargs):
            await asyncio.sleep(.1)
        plugin.context.llm_generate.side_effect = slow
        failed = await plugin.assess_age_protection("11111", self.event("我15岁"))
        self.assertEqual(failed["status"], "error")
        self.assertFalse(failed["decision_eligible"])
        self.assertEqual(plugin._tags_cache["11111"]["age_protection"], old)
        self.assertFalse(plugin._age_flights)

    async def test_policy_change_and_deletion_inflight_never_restore(self):
        for action in ("policy", "delete"):
            plugin = self.make_plugin(history_scan_enabled=False)
            started, release = asyncio.Event(), asyncio.Event()
            async def deferred(**kwargs):
                started.set()
                await release.wait()
                return await self.reply(**kwargs)
            plugin.context.llm_generate.side_effect = deferred
            task = asyncio.create_task(plugin.assess_age_protection("11111", self.event()))
            await started.wait()
            if action == "policy":
                plugin.config["llm_include_private_quotes"] = False
            else:
                plugin._user_generations["11111"] = 1
                plugin._tags_cache.pop("11111", None)
            release.set()
            result = await task
            self.assertFalse(result["decision_eligible"])
            self.assertNotIn("age_protection", plugin._tags_cache.get("11111", {}))

    async def test_budget_and_pages_are_bounded(self):
        plugin = self.make_plugin(age_protection_max_chars=500, history_scan_pages=100)
        manager = types.SimpleNamespace(get_filtered_conversations=AsyncMock(return_value=([], 1000)))
        plugin.context.conversation_manager = manager
        materials = await collect_materials(plugin, "11111", self.event("x" * 10000))
        self.assertEqual(sum(len(m["text"]) for m in materials), 500)
        manager.get_filtered_conversations.assert_awaited_once()

    async def test_newer_current_message_invalidates_old_inflight_verdict(self):
        plugin = self.make_plugin(history_scan_enabled=False)
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(**kwargs):
            if "我今年16岁" in kwargs["prompt"]:
                started.set()
                await release.wait()
            return await self.reply(**kwargs)
        plugin.context.llm_generate.side_effect = delayed
        old = asyncio.create_task(plugin.assess_age_protection("11111", self.event()))
        await started.wait()
        await plugin.assess_age_protection("11111", self.event("我今年20岁"))
        release.set()
        result = await old
        self.assertFalse(result["decision_eligible"])
        self.assertEqual(result["status"], "unknown")

    async def test_force_reassesses_after_bounded_interval_and_sparse_is_not_high(self):
        plugin = self.make_plugin(history_scan_enabled=False)
        event = self.event()
        await plugin.assess_age_protection("11111", event)
        plugin._tags_cache["11111"]["age_protection"]["assessed_at"] -= 6
        plugin._tags_cache["11111"]["age_protection_attempt"]["assessed_at"] -= 6
        await plugin.assess_age_protection("11111", event, force=True)
        self.assertEqual(plugin.context.llm_generate.await_count, 2)
        sparse = await plugin.assess_age_protection("11111", self.event("我在上高中"))
        self.assertFalse(sparse["decision_eligible"])
        self.assertEqual(sparse["minor_confidence"], 0)

    async def test_history_disabled_and_history_timeout_no_penalty(self):
        plugin = self.make_plugin(history_scan_enabled=False)
        manager = types.SimpleNamespace(get_filtered_conversations=AsyncMock())
        plugin.context.conversation_manager = manager
        await plugin.assess_age_protection("11111", self.event())
        manager.get_filtered_conversations.assert_not_awaited()
        plugin.config.update(history_scan_enabled=True, age_protection_timeout_seconds=.01)
        async def slow(**kwargs):
            await asyncio.sleep(.1)
        manager.get_filtered_conversations.side_effect = slow
        result = await plugin.assess_age_protection("11111", self.event())
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["decision_eligible"])

    async def test_public_projection_removes_age_and_evidence(self):
        plugin = self.make_plugin()
        raw = {"age_protection": {"minor_confidence": .99}, "risk_events": [1],
               "risk_dimensions": {"secret": 1}, "dimension_score": 60, "safety_concerns": [1]}
        for event in (None, self.event(), Event({})):
            result = plugin._public_profile_view(raw, event)
            self.assertTrue(all(key not in result for key in raw))


class AgeValidationTests(unittest.TestCase):
    def check(self, text, now=None, stamp=None, **overrides):
        now = now or int(time.time())
        material = dict(ref="valid", text=text, observed_at=stamp or now)
        raw = dict(status="ready", suspected_minor=True, minor_confidence=.99,
                   decision_eligible=True, identity_conflict=False,
                   evidence=[dict(ref="valid", kind="self_report_age", supports_minor=True)])
        raw.update(overrides)
        return sanitize(raw, [material], "fingerprint", now)

    def test_soft_third_person_quote_and_false_refs_never_eligible(self):
        for text in ("我上高中", "姐姐好", "我朋友16岁", "她说我16岁", "引用：我16岁", "我16岁是骗你的", '他说“我16岁”'):
            self.assertFalse(self.check(text)["decision_eligible"], text)
        self.assertFalse(self.check("我16岁", evidence=[dict(ref="invented", kind="self_report_age", supports_minor=True)])["decision_eligible"])

    def test_confidence_strict_finite_real_number(self):
        for value in (True, False, "0.99", float("nan"), float("inf"), -1, 1.1):
            self.assertFalse(self.check("我16岁", minor_confidence=value)["decision_eligible"])

    def test_adult_contradiction_account_change_and_old_age(self):
        now = int(time.time())
        self.assertFalse(self.check("我18岁")["decision_eligible"])
        self.assertFalse(self.check("我16岁", stamp=now - 3 * 365 * 86400)["decision_eligible"])
        self.assertTrue(self.check("我17岁")["decision_eligible"])
        self.assertTrue(self.check("我16岁但账号换人了")["identity_conflict"])
        self.assertFalse(self.check("我16岁但账号换人了")["decision_eligible"])
        raw = dict(status="ready", suspected_minor=True, minor_confidence=.99, decision_eligible=True,
                   identity_conflict=False, evidence=[dict(ref="minor", kind="self_report_age", supports_minor=True)])
        materials = [dict(ref="minor", text="我16岁", observed_at=now), dict(ref="adult", text="我已成年了", observed_at=now)]
        result = sanitize(raw, materials, "fp", now)
        self.assertTrue(result["identity_conflict"])
        self.assertFalse(result["decision_eligible"])

    def test_birth_date_changes_to_adult_over_time(self):
        from datetime import datetime, timezone
        before = int(datetime(2026, 10, 29, tzinfo=timezone.utc).timestamp())
        after = int(datetime(2026, 10, 30, tzinfo=timezone.utc).timestamp())
        self.assertEqual(hard_age("我出生于2008年10月30日", before, before), (True, "birth_date"))
        self.assertEqual(hard_age("我出生于2008年10月30日", before, after), (False, "birth_date"))
