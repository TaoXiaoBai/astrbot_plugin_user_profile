import asyncio
import json
import time
import types
import unittest
from unittest.mock import AsyncMock

import test_plugin
from test_plugin import Event, _new_stat
from profile_core import normalize_quote, quota_quotes, quote_scene
from scene_profiles import cached_view, policy


class SceneTests(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self, config=None):
        return test_plugin.PluginTests.make_plugin(self, {"cross_scene_link_enabled": True, "history_scan_enabled": False,
                                            "friend_reconcile_enabled": False, **(config or {})})

    async def prepared(self):
        plugin = self.make_plugin()
        await plugin._ensure_loaded()
        plugin._stats["11111"] = dict(_new_stat(), g_count=2, p_count=2)
        plugin._quotes["11111"] = [normalize_quote({"src": "群 22222", "t": int(time.time()), "text": "PUBLIC_RAW"}),
                                    normalize_quote({"src": "私聊", "t": int(time.time()), "text": "PRIVATE_RAW"})]
        async def branch(qq, st, quotes, *args, **kwargs):
            name = quote_scene(quotes[0])
            return {"tags": [{"tag": "friendly", "confidence": 0.9, "reason": name + "_EVIDENCE"}],
                    "impression": name + "_IMPRESSION", "traits": [name + "_TRAIT"]}
        plugin._tag_engine.generate_llm_tags = AsyncMock(side_effect=branch)
        plugin.context.llm_generate = AsyncMock(return_value=types.SimpleNamespace(completion_text=json.dumps({
            "tags": [], "impression": "FUSED_PRIVATE_DERIVED", "traits": ["FUSED_TRAIT"],
            "common_traits": "COMMON", "differences": "DIFFERENCE", "confidence": "仅群侧或私侧不足时注明"})))
        return plugin

    async def test_independent_material_quota_and_sources(self):
        plugin = self.make_plugin({"quote_keep": 2})
        for scene in ("private", "group", "group", "group"):
            await plugin.on_message(Event({"post_type": "message", "message_type": scene, "user_id": 11111},
                                          group="22222" if scene == "group" else "", text=scene))
        quotes = plugin._quotes["11111"]
        self.assertEqual([q["scene"] for q in quotes].count("private"), 1)
        self.assertEqual([q["scene"] for q in quotes].count("group"), 2)
        self.assertEqual(quotes[-1]["group_id"], "22222")
        self.assertEqual(quote_scene({"src": "历史会话"}), "unknown")
        self.assertLessEqual(len(quota_quotes(quotes * 10, 2)), 4)
        await plugin.terminate()

    async def test_branches_fusion_and_dedup(self):
        plugin = await self.prepared()
        result = await plugin.get_profile_tags_with_score("11111", Event({}, group=""))
        self.assertEqual(result["scene"], "fused")
        self.assertEqual(result["impression"], "FUSED_PRIVATE_DERIVED")
        self.assertIn("群聊表现", result["sections"])
        self.assertIn("私聊表现", result["sections"])
        self.assertEqual(len([t for t in result["tags"] if t["tag"] == "friendly"]), 1)
        for call in plugin._tag_engine.generate_llm_tags.await_args_list:
            self.assertEqual(len({quote_scene(q) for q in call.args[2]}), 1)
        before = plugin.context.llm_generate.await_count
        await plugin.get_profile_tags_with_score("11111", Event({}, group=""))
        self.assertEqual(plugin.context.llm_generate.await_count, before)
        self.assertEqual(plugin._calc_risk_score(result["tags"] * 2), result["score"])

    async def test_public_text_tool_image_model_ranking_and_api_do_not_leak_derived_text(self):
        plugin = await self.prepared()
        await plugin.get_profile_tags_with_score("11111", Event({}, group=""))
        public = Event({}, group="22222")
        plugin._fetch_stranger_info = AsyncMock(return_value={})
        plugin._prepare_avatar_bytes = AsyncMock(return_value=None)
        model = await plugin._build_profile_model("11111", public)
        text = await plugin._build_profile_text("11111", public)
        decision = await plugin.get_decision_profile("11111", public)
        ranking = plugin._assemble_lightweight_snapshot("11111", plugin._stats["11111"], {}, [])
        tool = await plugin.user_profile_query(public, "11111")
        other = await plugin.get_profile_tags_with_score("11111", Event({}, sender="33333", group=""))
        for value in (model, text, decision, ranking, tool, other):
            encoded = str(value)
            for marker in ("PRIVATE_RAW", "private_IMPRESSION", "private_TRAIT", "private_EVIDENCE", "FUSED_PRIVATE_DERIVED", "FUSED_TRAIT"):
                self.assertNotIn(marker, encoded)
        self.assertIn("group_IMPRESSION", text)
        self.assertEqual(model["sections"], {})

    async def test_policy_change_invalidates_without_refresh_wait_or_prior(self):
        plugin = await self.prepared()
        event = Event({}, group="")
        await plugin.get_profile_tags_with_score("11111", event)
        plugin.config["llm_include_private_quotes"] = False
        result = await plugin.get_profile_tags_with_score("11111", event)
        for marker in ("private_IMPRESSION", "private_TRAIT", "private_EVIDENCE"):
            self.assertNotIn(marker, str(result))
        self.assertNotIn("FUSED_", str(result))
        self.assertIsNone(plugin._tag_engine.generate_llm_tags.await_args.kwargs["prior"])
        self.assertIn("PRIVATE_RAW", str(plugin._quotes))

    async def test_injection_switches_are_read_only(self):
        plugin = await self.prepared()
        await plugin.get_profile_tags_with_score("11111", Event({}, group=""))
        plugin._ensure_history_scanned = AsyncMock(side_effect=AssertionError("scan"))
        plugin.context.llm_generate = AsyncMock(side_effect=AssertionError("LLM"))
        event = Event({}, group="22222")
        linked = await plugin._build_conversation_profile_context("11111", event)
        self.assertIn("FUSED_PRIVATE_DERIVED", linked)
        self.assertNotIn("PRIVATE_RAW", linked)
        self.assertIn("private_IMPRESSION", linked)
        self.assertIn("group_IMPRESSION", linked)
        self.assertIn("融合印象", linked)
        plugin.config["group_chat_use_linked_impression"] = False
        public = await plugin._build_conversation_profile_context("11111", event)
        self.assertIn("group_IMPRESSION", public)
        self.assertNotIn("FUSED_", public)
        plugin.config["llm_include_private_quotes"] = False
        self.assertNotIn("FUSED_", await plugin._build_conversation_profile_context("11111", event))
        plugin._ensure_history_scanned.assert_not_awaited()
        plugin.context.llm_generate.assert_not_awaited()

    async def test_legacy_and_unknown_never_become_group_prior(self):
        plugin = await self.prepared()
        plugin._tags_cache["11111"] = {"status": "success", "impression": "LEGACY_PRIVATE", "tags": []}
        plugin._quotes["11111"].append({"src": "历史会话", "text": "UNKNOWN_SECRET"})
        result = await plugin.get_profile_tags_with_score("11111", Event({}))
        self.assertNotIn("LEGACY_PRIVATE", str(result))
        call = plugin._tag_engine.generate_llm_tags.await_args
        self.assertIsNone(call.kwargs["prior"])
        self.assertNotIn("UNKNOWN_SECRET", str(call.args[2]))
        self.assertIn("legacy", plugin._tags_cache["11111"])

    async def test_single_side_empty_and_fusion_failure_preserve_available_results(self):
        plugin = await self.prepared()
        plugin._quotes["11111"] = plugin._quotes["11111"][:1]
        plugin.context.llm_generate = AsyncMock(side_effect=RuntimeError("offline"))
        result = await plugin.get_profile_tags_with_score("11111", Event({}, group=""))
        self.assertEqual(result["impression"], "group_IMPRESSION")
        self.assertIn("群聊表现", result["sections"])
        self.assertNotIn("私聊表现", result["sections"])
        self.assertEqual(len(plugin._tag_engine.generate_llm_tags.await_args_list), 1)
        self.assertEqual(await plugin.get_profile_tags("55555"), [])

    async def test_history_private_policy_unknown_is_not_saved_or_used(self):
        for setting in ("collect_private", "store_private_quotes", "store_quotes"):
            plugin = self.make_plugin({setting: False})
            conv = types.SimpleNamespace(history=json.dumps([{"role": "user", "content": "name(ID: 11111): PRIVATE_HISTORY"}]), created_at=1, updated_at=2)
            plugin.context.conversation_manager = types.SimpleNamespace(get_filtered_conversations=AsyncMock(return_value=([conv], 1)))
            await plugin._ensure_loaded()
            scanned = await plugin._scan_history("11111")
            self.assertEqual(scanned["quotes"], [])

    async def test_slot_concurrency_policy_race_and_deletion(self):
        plugin = await self.prepared()
        entered = asyncio.Event()
        release = asyncio.Event()
        async def generate(*args, **kwargs):
            entered.set()
            await release.wait()
            return {"impression": "LATE_PRIVATE", "tags": [], "traits": []}
        plugin._tag_engine.generate_llm_tags = AsyncMock(side_effect=generate)
        event = Event({}, group="")
        tasks = [asyncio.create_task(plugin.get_profile_tags_with_score("11111", event)) for _ in range(2)]
        await asyncio.wait_for(entered.wait(), 2)
        plugin._delete_profile_locked("11111")
        release.set()
        await asyncio.gather(*tasks)
        self.assertNotIn("11111", plugin._tags_cache)
        self.assertEqual(plugin._tag_engine.generate_llm_tags.await_count, 1)

    async def test_policy_change_while_private_analysis_inflight_does_not_restore_cache(self):
        plugin = await self.prepared()
        plugin._quotes["11111"] = plugin._quotes["11111"][1:]
        entered, release = asyncio.Event(), asyncio.Event()
        async def generate(*args, **kwargs):
            entered.set()
            await release.wait()
            return {"impression": "LATE_SECRET", "tags": [], "traits": []}
        plugin._tag_engine.generate_llm_tags = AsyncMock(side_effect=generate)
        task = asyncio.create_task(plugin.get_profile_tags_with_score("11111", Event({}, group="")))
        await asyncio.wait_for(entered.wait(), 2)
        plugin.config["llm_include_private_quotes"] = False
        release.set()
        result = await task
        self.assertNotIn("LATE_SECRET", str(result))
        self.assertNotIn("private", (plugin._tags_cache.get("11111") or {}).get("slots", {}))
        plugin.context.llm_generate.assert_not_awaited()

    async def test_group_and_private_concurrent_requests_have_separate_versions(self):
        plugin = await self.prepared()
        plugin.config["cross_scene_link_enabled"] = False
        await asyncio.gather(plugin.get_profile_tags_with_score("11111", Event({})),
                             plugin.get_profile_tags_with_score("11111", Event({}, group="")))
        slots = plugin._tags_cache["11111"]["slots"]
        self.assertEqual(slots["group"]["impression"], "group_IMPRESSION")
        self.assertEqual(slots["private"]["impression"], "private_IMPRESSION")
        self.assertEqual(plugin._tag_engine.generate_llm_tags.await_count, 2)

    async def test_confirmed_legacy_history_sources_and_timestamps_are_preserved(self):
        plugin = self.make_plugin({"history_scan_enabled": True, "history_scan_cooldown": 0})
        def conv(origin, text):
            return types.SimpleNamespace(user_id=origin, created_at=100, updated_at=200,
                history=json.dumps([{"role": "user", "content": "name(ID: 11111): " + text}]))
        plugin.context.conversation_manager = types.SimpleNamespace(get_filtered_conversations=AsyncMock(return_value=(
            [conv("adapter:GroupMessage:22222", "GROUP_HISTORY"), conv("adapter:FriendMessage:11111", "PRIVATE_HISTORY"), conv("", "UNKNOWN_HISTORY")], 3)))
        await plugin._ensure_history_scanned("11111", force=True)
        quotes = plugin._quotes["11111"]
        self.assertEqual({q["scene"] for q in quotes}, {"group", "private", "unknown"})
        self.assertEqual({q["t"] for q in quotes}, {200})
        self.assertEqual(next(q for q in quotes if q["scene"] == "group")["group_id"], "22222")
        await plugin.terminate()
