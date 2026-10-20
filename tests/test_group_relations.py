import asyncio
import copy
import importlib.util
import pathlib
import sys
import time
import types
import unittest
from unittest.mock import AsyncMock

import test_plugin as fixtures
from test_plugin import Event
from group_relations import RELATION_MARKER, RELATIONS_KEY
from scene_profiles import policy


class GroupRelationTests(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self, config=None):
        return fixtures.PluginTests.make_plugin(self, {"cross_scene_link_enabled": True, **(config or {})})

    def record(self, **updates):
        result = dict(platform_id="snowluma", self_id="99999", group_id="22222", inviter="11111", operator="33333",
                      request_key="req", membership="IN", join_time=int(time.time()), confirmed_at=int(time.time()),
                      evidence="真实 bot notice", revision=1, event_key="join", event_created_at_ms=int(time.time() * 1000))
        result.update(updates)
        return result

    async def test_persistent_scoped_idempotent_out_of_order(self):
        p = self.make_plugin()
        self.assertEqual((await p.record_bot_group_relation(self.record()))["status"], "recorded")
        self.assertEqual((await p.record_bot_group_relation(self.record()))["status"], "duplicate")
        await p.record_bot_group_relation(self.record(revision=2, membership="OUT", event_key="leave"))
        await p.record_bot_group_relation(self.record(revision=1, event_key="stale"))
        await p.record_bot_group_relation(self.record(self_id="88888", inviter="44444", event_key="other-bot"))
        await p.record_bot_group_relation(self.record(platform_id="other", inviter="55555", event_key="other-platform"))
        restarted = self.make_plugin()
        restarted._kv = copy.deepcopy(p._kv)
        own = await restarted.get_bot_group_relations(platform_id="snowluma", self_id="99999", group_id="22222")
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["membership"], "OUT")
        self.assertEqual(await restarted.get_bot_group_relations(platform_id="snowluma", self_id="99999", inviter="33333"), [])

    async def test_context_scope_private_direction_and_expiry(self):
        p = self.make_plugin()
        for kwargs in ({}, {"group_id": "77777", "inviter": "44444", "event_key": "unrelated"}, {"self_id": "88888", "group_id": "66666", "event_key": "wrongbot"}):
            await p.record_bot_group_relation(self.record(**kwargs))
        group = await p._build_conversation_profile_context("11111", Event({}))
        self.assertIn("22222", group)
        self.assertNotIn("77777", group)
        self.assertNotIn("66666", group)
        private = await p._build_conversation_profile_context("11111", Event({}, group=""))
        self.assertIn("22222", private)
        self.assertNotIn("77777", private)
        operator = await p._build_conversation_profile_context("33333", Event({}, sender="33333", group=""))
        self.assertNotIn("群 22222", operator)
        await p.record_bot_group_relation(self.record(revision=2, event_key="old", confirmed_at=int(time.time())-90000))
        self.assertIn("最近确认在群，当前待核实", await p._build_conversation_profile_context("11111", Event({})))

    async def test_context_sections_policy_and_no_models_or_scans(self):
        p = self.make_plugin()
        await p._ensure_loaded()
        p._tags_cache["11111"] = {"slots": {name: {"policy": list(policy(p)), "status": "success", "impression": name+"_SUMMARY"} for name in ("group", "private", "fused")}}
        p.context.llm_generate = AsyncMock(side_effect=AssertionError("LLM"))
        p._ensure_history_scanned = AsyncMock(side_effect=AssertionError("scan"))
        for group in ("22222", ""):
            text = await p._build_conversation_profile_context("11111", Event({}, group=group))
            for name in ("group", "private", "fused"):
                self.assertIn(name+"_SUMMARY", text)
        for option in ("cross_scene_link_enabled", "group_chat_use_linked_impression", "llm_include_private_quotes"):
            p.config[option] = False
            text = await p._build_conversation_profile_context("11111", Event({}))
            self.assertNotIn("private_SUMMARY", text)
            self.assertNotIn("fused_SUMMARY", text)
            p.config[option] = True
        p._tags_cache.clear()
        self.assertIn("可用信息不足", await p._build_conversation_profile_context("11111", Event({})))
        p.context.llm_generate.assert_not_awaited()
        p._ensure_history_scanned.assert_not_awaited()

    async def test_write_failure_retry_and_delete_cutoff_restart(self):
        p = self.make_plugin()
        original = p.put_kv_data
        async def fail(key, value):
            if key == RELATIONS_KEY:
                raise OSError("disk")
            await original(key, value)
        p.put_kv_data = fail
        record = self.record(event_created_at_ms=int(time.time()*1000)-1000)
        self.assertEqual((await p.record_bot_group_relation(record))["status"], "failure")
        self.assertEqual(p._group_relations, {})
        p.put_kv_data = original
        await p.record_bot_group_relation(record)
        await p.delete_profile_command(Event({}, group="", text="/画像删除"))
        self.assertFalse(p._group_relations)
        restarted = self.make_plugin()
        restarted._kv = copy.deepcopy(p._kv)
        self.assertEqual((await restarted.record_bot_group_relation(record))["status"], "cancelled")
        new = self.record(revision=2, membership="OUT", event_key="new", event_created_at_ms=int(time.time()*1000)+10)
        self.assertEqual((await restarted.record_bot_group_relation(new))["status"], "recorded")

    async def test_delete_waits_for_inflight_and_eviction_cleans_relation(self):
        p = self.make_plugin()
        started, release = asyncio.Event(), asyncio.Event()
        original = p.put_kv_data
        async def slow(key, value):
            if key == RELATIONS_KEY and value:
                started.set()
                await release.wait()
            await original(key, value)
        p.put_kv_data = slow
        record = self.record()
        writing = asyncio.create_task(p.record_bot_group_relation(record))
        await started.wait()
        deleting = asyncio.create_task(p.delete_profile_command(Event({}, group="", text="/画像删除")))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(writing, deleting)
        self.assertFalse(p._group_relations)
        self.assertEqual((await p.record_bot_group_relation(record))["status"], "cancelled")
        await p.record_bot_group_relation(self.record(event_created_at_ms=int(time.time()*1000)+10, revision=2, event_key="future"))
        p._stats["11111"] = {"g_last": 1}
        p._prune_oldest(0)
        self.assertFalse(p._group_relations)

    async def test_long_impressions_cannot_evict_relation_claims_or_safety(self):
        p = self.make_plugin()
        await p.record_bot_group_relation(self.record())
        p._tags_cache["11111"] = {"slots": {scene: {"policy": list(policy(p)), "status": "success", "impression": "长概括" * 300} for scene in ("group", "private", "fused")}}
        for limit in (300, 360, 900):
            p.config["llm_context_max_chars"] = limit
            text = await p._build_conversation_profile_context("11111", Event({}))
            self.assertLessEqual(len(text), limit)
            self.assertIn(RELATION_MARKER, text)
            self.assertIn('astrbot-bot-group-claims:{"22222":1}', text)
            self.assertIn("群 22222", text)
            self.assertIn("11111", text)
            self.assertIn("近期确认在群", text)
            self.assertIn("不是指令或人格", text)
            self.assertIn("不得外泄私聊或自动处罚", text)

    async def test_partial_claims_preserve_missing_and_newer_guard_relations(self):
        p = self.make_plugin()
        await p.record_bot_group_relation(self.record())
        for revision, retained in ((1, False), (2, True)):
            req = types.SimpleNamespace(system_prompt='<guard_group_relations>\n<!-- guard-group-relation:{"22222":' + str(revision) + '} -->covered-or-newer\n<!-- guard-group-relation:{"77777":1} -->pending-missing\n</guard_group_relations>')
            await p.on_llm_request(Event({}, group=""), req)
            self.assertIn("pending-missing", req.system_prompt)
            self.assertEqual("covered-or-newer" in req.system_prompt, retained)
            self.assertIn("<guard_group_relations>", req.system_prompt)
        req = types.SimpleNamespace(system_prompt='<guard_group_relations>\n<!-- guard-group-relation:{"22222":1} -->covered\nlegacy-missing\n</guard_group_relations>')
        await p.on_llm_request(Event({}), req)
        self.assertIn("legacy-missing", req.system_prompt)
        self.assertNotIn("-->covered", req.system_prompt)
        p = self.make_plugin()
        req = types.SimpleNamespace(system_prompt="<guard_group_relations>legacy-unclaimed</guard_group_relations>")
        await p.on_llm_request(Event({}), req)
        self.assertIn("legacy-unclaimed", req.system_prompt)
        self.assertNotIn(RELATION_MARKER, req.system_prompt)

    async def test_marker_removes_only_guard_fallback(self):
        p = self.make_plugin()
        await p.record_bot_group_relation(self.record())
        req = types.SimpleNamespace(system_prompt='guard blacklist rule\n<guard_group_relations>\n<!-- guard-group-relation:{"22222":1} -->old relation\n</guard_group_relations>')
        await p.on_llm_request(Event({}), req)
        self.assertIn("guard blacklist rule", req.system_prompt)
        self.assertNotIn("old relation", req.system_prompt)
        self.assertEqual(req.system_prompt.count(RELATION_MARKER), 1)
        before = req.system_prompt
        await p.on_llm_request(Event({}), req)
        self.assertEqual(before, req.system_prompt)

    async def test_guard_to_profile_real_delivery_recovery_delete_and_markers(self):
        directory = pathlib.Path(__file__).resolve().parents[2] / "astrbot_plugin_group_invite_guard"
        package = types.ModuleType("relation_guard_integration")
        package.__path__ = [str(directory)]
        sys.modules[package.__name__] = package
        spec = importlib.util.spec_from_file_location(package.__name__+".main", directory / "main.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        guard = module.GroupInviteGuardPlugin.__new__(module.GroupInviteGuardPlugin)
        guard.config = {}
        guard._kv = {}
        async def get(key, default): return copy.deepcopy(guard._kv.get(key, default))
        async def put(key, value): guard._kv[key] = copy.deepcopy(value)
        guard.get_kv_data, guard.put_kv_data = get, put
        profile = self.make_plugin()
        guard._get_profile_plugin_instance = lambda: profile
        guard._read_ban_entries = lambda: [("55555", "blacklist rule")]
        original = profile.put_kv_data
        async def fail(key, value): raise OSError("temporary")
        profile.put_kv_data = fail
        await guard.record_bot_group_relation(platform_id="snowluma", self_id="99999", group_id="22222", membership="IN", evidence="notice", event_key="integration", inviter="11111")
        self.assertTrue(guard._kv["bot_group_relations_v1"]["pending"])
        self.assertFalse(profile._group_relations)
        for hooks in ((profile.on_llm_request, guard.on_llm_request), (guard.on_llm_request, profile.on_llm_request)):
            pending_req = types.SimpleNamespace(system_prompt="base")
            for hook in hooks:
                await hook(Event({}), pending_req)
            self.assertIn("<guard_group_relations>", pending_req.system_prompt)
            self.assertIn("群 22222", pending_req.system_prompt)
            self.assertIn("blacklist rule", pending_req.system_prompt)
            self.assertNotIn(RELATION_MARKER, pending_req.system_prompt)
        profile.put_kv_data = original
        await guard._deliver_group_relations()
        self.assertFalse(guard._kv["bot_group_relations_v1"]["pending"])
        self.assertEqual(len(profile._group_relations), 1)
        event = Event({})
        req = types.SimpleNamespace(system_prompt="base")
        await guard.on_llm_request(event, req)
        self.assertIn("guard_group_relations", req.system_prompt)
        await profile.on_llm_request(event, req)
        self.assertNotIn("guard_group_relations", req.system_prompt)
        self.assertIn("blacklist rule", req.system_prompt)
        self.assertEqual(req.system_prompt.count(RELATION_MARKER), 1)
        old_record = next(iter(guard._kv["bot_group_relations_v1"]["records"].values()))
        await profile.delete_profile_command(Event({}, group="", text="/画像删除"))
        guard._kv["bot_group_relations_v1"]["pending"][old_record["event_key"]] = old_record
        await guard._deliver_group_relations()
        self.assertFalse(profile._group_relations)
        self.assertFalse(guard._kv["bot_group_relations_v1"]["pending"])
        await asyncio.sleep(0.002)
        await guard.record_bot_group_relation(platform_id="snowluma", self_id="99999", group_id="22222", membership="OUT", evidence="leave notice", event_key="new-lifecycle")
        self.assertEqual(len(profile._group_relations), 1)
        self.assertEqual(next(iter(profile._group_relations.values()))["membership"], "OUT")
        req = types.SimpleNamespace(system_prompt="base")
        await profile.on_llm_request(event, req)
        await guard.on_llm_request(event, req)
        self.assertNotIn("guard_group_relations", req.system_prompt)
        self.assertIn("blacklist rule", req.system_prompt)
        self.assertEqual(req.system_prompt.count(RELATION_MARKER), 1)
