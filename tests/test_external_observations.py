import asyncio
import copy
import time
import unittest
from unittest.mock import AsyncMock, patch

import test_plugin as fixtures
import external_observations as external


def report(incident="test-1"):
    return {"schema_version": 1, "source": "astrbot_plugin_insult_guard", "kind": "group_environment",
            "verification": "independent_double_review", "platform_id": "offline", "self_id": "99999",
            "group_id": "22222", "scene": "group", "qq": "11111", "incident_id": incident,
            "evidence": [{"message_id": "3", "text": "你这个人太过分了", "time": time.time(),
                          "severity": 1, "reason": "待画像独立审核",
                          "context": [{"sender_id": "33333", "text": "请不要再骂我"}]}]}


class ExternalObservationTests(unittest.IsolatedAsyncioTestCase):
    def plugin(self, **config):
        plugin = fixtures.PluginTests.make_plugin(self, {"external_observation_enable": True,
                                               "history_scan_enabled": False, **config})
        plugin._ensure_flush_task = lambda: None
        self.addAsyncCleanup(plugin.terminate)
        return plugin

    async def test_queued_persisted_and_independent_refresh_no_message_count(self):
        plugin = self.plugin()
        with patch.object(external, "slot_analysis", new_callable=AsyncMock) as analyze:
            result = await plugin.request_external_observation(report())
            self.assertEqual(result["status"], "queued")
            await plugin._external_observation_queue.join()
            self.assertEqual(len(plugin._kv["up_stats"]["11111"]["external_observations"]), 1)
            self.assertEqual(plugin._stats["11111"].get("g_count", 0), 0)
            args = analyze.await_args.args
            self.assertEqual(args[2], "group")
            self.assertEqual(args[3][0]["external_context"][0]["sender_id"], "33333")
            self.assertEqual(args[3][0]["text"], "你这个人太过分了")
            self.assertTrue(analyze.await_args.kwargs["force_refresh"])
            self.assertEqual(plugin._quotes, {})

    async def test_disabled_private_invalid_or_collection_off_rejected(self):
        plugin = self.plugin(external_observation_enable=False)
        self.assertEqual((await plugin.request_external_observation(report()))["status"], "disabled")
        plugin.config["external_observation_enable"] = True
        raw = report()
        raw["scene"] = "private"
        self.assertEqual((await plugin.request_external_observation(raw))["status"], "invalid_report")
        raw = report()
        raw["evidence"][0]["time"] -= 1000
        self.assertEqual((await plugin.request_external_observation(raw))["status"], "invalid_report")
        plugin.config["store_quotes"] = False
        self.assertEqual((await plugin.request_external_observation(report()))["status"], "collection_disabled")

    async def test_dedupe_cooldown_and_record_capacity(self):
        plugin = self.plugin()
        with patch.object(external, "slot_analysis", new_callable=AsyncMock) as analyze:
            await plugin.request_external_observation(report())
            await plugin._external_observation_queue.join()
            self.assertEqual((await plugin.request_external_observation(report()))["status"], "duplicate")
            self.assertEqual((await plugin.request_external_observation(report("test-2")))["status"], "recorded_cooldown")
            for index in range(30):
                await plugin.request_external_observation(report(str(index)))
            self.assertEqual(len(plugin._stats["11111"]["external_observations"]), 20)
            self.assertEqual(analyze.await_count, 1)

    async def test_delete_invalidates_queued_work(self):
        plugin = self.plugin()
        await plugin._ensure_loaded()
        with patch.object(external, "slot_analysis", new_callable=AsyncMock) as analyze:
            await plugin.request_external_observation(report())
            async with plugin._store_lock:
                plugin._delete_profile_locked("11111")
            await plugin._external_observation_queue.join()
            analyze.assert_not_awaited()
            self.assertNotIn("11111", plugin._stats)

    async def test_retention_removes_external_originals_and_context(self):
        plugin = self.plugin(quote_retention_days=1)
        with patch.object(external, "slot_analysis", new_callable=AsyncMock):
            await plugin.request_external_observation(report())
            await plugin._external_observation_queue.join()
        plugin._stats["11111"]["external_observations"][0]["received_at"] -= 90000
        self.assertEqual(plugin._apply_quote_retention_locked(int(time.time())), 1)
        self.assertEqual(plugin._stats["11111"]["external_observations"], [])

    async def test_ordinary_material_merge_preserves_context_and_never_private_material(self):
        plugin = self.plugin()
        await plugin._ensure_loaded()
        st = {"external_observations": [{"received_at": time.time(), "report": report()}]}
        quotes = [{"text": "你这个人太过分了", "group_id": "22222", "scene": "group", "t": int(time.time())}]
        merged = plugin._external_group_materials(st, quotes)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]["external_observation"])
        self.assertEqual(merged[0]["external_context"][0]["text"], "请不要再骂我")

    async def test_guard_delivery_to_real_profile_queue_and_model_prompt(self):
        import importlib
        import pathlib
        import sys
        import types
        plugin = self.plugin(llm_provider_id="offline")
        plugin.context.llm_generate = AsyncMock(return_value=types.SimpleNamespace(completion_text='{"tags":[],"impression":"仅有一次群聊争执，证据不足以推断稳定特点","traits":[]}'))
        guard_path = pathlib.Path(__file__).resolve().parents[2] / "astrbot_plugin_insult_guard"
        if not (guard_path / "external_detection.py").is_file():
            self.skipTest("optional insult guard integration fixture is not installed")
        package = types.ModuleType("offline_feedback_guard")
        package.__path__ = [str(guard_path)]
        with patch.dict(sys.modules, {package.__name__: package}):
            module = importlib.import_module(package.__name__ + ".external_detection")
            runtime = importlib.import_module(package.__name__ + ".runtime")
            guard = module.ExternalDetectionMixin()
            guard.config = {"profile_feedback_enable": True}
            guard.context = types.SimpleNamespace(get_registered_star=lambda _: types.SimpleNamespace(star_cls=plugin))
            guard.diagnostic = lambda _: None
            snap = runtime.Snapshot("offline", "99999", "group", "22222", "11111", "offline", "3", "你这个人太过分了", False, "", "offline:GroupMessage:22222")
            messages = [{"message_id": "3", "sender_id": "11111", "text": snap.text, "time": time.time()}]
            plan = [{"message_id": "3", "sender_id": "11111", "severity": 1, "reason": "守卫结论不能直接作为画像标签"}]
            result = await guard._profile_feedback(snap, messages, plan, "group_environment", "integration-1")
            self.assertEqual(result["users"][0]["status"], "queued")
            await plugin._external_observation_queue.join()
        self.assertEqual(plugin.context.llm_generate.await_count, 1)
        prompt = plugin.context.llm_generate.await_args.kwargs["prompt"]
        self.assertIn(snap.text, prompt)
        self.assertIn("不是已成立的画像标签", prompt)
        self.assertNotIn("守卫结论不能直接作为画像标签", prompt)
        self.assertEqual(plugin._tags_cache["11111"]["slots"]["group"]["status"], "success")
        self.assertEqual(plugin._tags_cache["11111"]["slots"]["group"]["tags"], [])

    async def test_storage_failure_rolls_back_and_can_resubmit(self):
        plugin = self.plugin()
        await plugin._ensure_loaded()
        original = plugin.put_kv_data
        plugin.put_kv_data = AsyncMock(side_effect=OSError("offline"))
        with self.assertRaises(OSError):
            await plugin.request_external_observation(report())
        self.assertNotIn("11111", plugin._stats)
        plugin.put_kv_data = original
        with patch.object(external, "slot_analysis", new_callable=AsyncMock):
            self.assertEqual((await plugin.request_external_observation(report()))["status"], "queued")
            await plugin._external_observation_queue.join()

    async def test_analysis_disabled_records_without_queue_or_model(self):
        plugin = self.plugin(llm_tags=False)
        result = await plugin.request_external_observation(report())
        self.assertEqual(result["status"], "recorded_analysis_disabled")
        self.assertEqual(len(plugin._stats["11111"]["external_observations"]), 1)
        self.assertTrue(plugin._external_observation_queue.empty())
        self.assertIsNone(plugin._external_observation_task)
