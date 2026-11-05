import time
import unittest
from unittest.mock import AsyncMock

import test_plugin
from test_plugin import Event


class TeaseGuardTests(unittest.IsolatedAsyncioTestCase):
    make_plugin = test_plugin.PluginTests.make_plugin

    def setUp(self):
        self.plugin = self.make_plugin({"history_scan_enabled": False})
        self.plugin._send_profile = AsyncMock()
        self.event = Event({}, sender="11111", group="")
        self.plugin._tags_cache = {"11111": {"slots": {"group": {"tags": [
            {"tag": "troll", "confidence": 0.9, "source": "llm"},
            {"tag": "friendly", "confidence": 0.8, "source": "llm"},
        ]}}}}

    async def query_self(self):
        await self.plugin._dispatch_chat_query(self.event, "self", "test_entry")

    def sent_text(self):
        result = []
        for chain in self.event.sent:
            for kind, value in chain:
                if kind == "plain":
                    result.append(value)
        return "\n".join(result)

    async def test_blocks_only_after_threshold_with_risk_tags(self):
        await self.query_self()
        await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 2)
        self.assertEqual(self.event.sent, [])
        await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 2)
        self.assertTrue(self.sent_text())
        self.assertNotIn("【用户画像】", self.sent_text())

    async def test_keeps_blocking_subsequent_queries_in_window(self):
        for _ in range(5):
            await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 2)

    async def test_friendly_user_is_never_blocked(self):
        self.plugin._tags_cache = {"11111": {"slots": {"group": {"tags": [
            {"tag": "friendly", "confidence": 0.9, "source": "llm"}]}}}}
        for _ in range(5):
            await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 5)
        self.assertEqual(self.event.sent, [])

    async def test_low_confidence_risk_below_threshold(self):
        self.plugin._tags_cache = {"11111": {"slots": {"group": {"tags": [
            {"tag": "troll", "confidence": 0.59, "source": "llm"}]}}}}
        for _ in range(4):
            await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 4)

    async def test_configurable_confidence_threshold(self):
        self.plugin.config["tease_guard_min_confidence"] = 0.95
        for _ in range(4):
            await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 4)
        self.plugin.config["tease_guard_min_confidence"] = 0.6
        await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 4)

    async def test_disabled_switch_passes_through(self):
        self.plugin.config["tease_guard_enabled"] = False
        for _ in range(5):
            await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 5)

    async def test_admin_self_query_is_never_blocked(self):
        self.event.is_admin = lambda: True
        for _ in range(5):
            await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 5)

    async def test_querying_other_user_never_blocks_or_counts(self):
        # 管理员查他人可通过权限判断，且不会触发只针对本人查询的趣味拦截。
        other = Event({}, sender="22222", group="")
        other.is_admin = lambda: True
        for _ in range(5):
            await self.plugin._dispatch_chat_query(other, "11111", "test_entry")
        self.assertEqual(self.plugin._send_profile.await_count, 5)
        self.assertEqual(other.sent, [])
        self.assertNotIn("22222", self.plugin._self_query_log)

    async def test_query_threshold_is_configurable(self):
        self.plugin.config["tease_guard_min_queries"] = 2
        await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 1)
        await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 1)
        self.assertTrue(self.sent_text())

    async def test_window_expiry_resets_counter(self):
        await self.query_self()
        await self.query_self()
        self.plugin._self_query_log["11111"][:] = [
            time.time() - self.plugin.config["tease_guard_window_seconds"] - 1
        ] * 2
        await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 3)
        self.assertEqual(self.event.sent, [])

    async def test_custom_message_and_stop_event(self):
        self.plugin.config["tease_guard_message"] = "自定义：先去冷静一下。"
        for _ in range(3):
            await self.query_self()
        self.assertEqual(self.sent_text(), "自定义：先去冷静一下。")

    async def test_shortcut_commands_funnel_into_guard(self):
        await self.plugin.self_profile_command_short(Event({}, sender="11111", group=""))
        await self.plugin.self_profile_command(Event({}, sender="11111", group=""))
        await self.plugin.profile_command(Event({}, text="画像 自己", sender="11111", group=""))
        self.assertEqual(self.plugin._send_profile.await_count, 2)

    async def test_all_command_entries_share_guard_flow(self):
        self.event.sent = []
        await self.plugin.profile_command(Event({}, text="画像 自己", sender="11111", group=""))
        await self.plugin.profile_command(Event({}, text="画像 自己", sender="11111", group=""))
        self.assertEqual(self.plugin._send_profile.await_count, 2)
        await self.plugin.profile_command(Event({}, text="画像 自己", sender="11111", group=""))
        self.assertEqual(self.plugin._send_profile.await_count, 2)

    async def test_empty_or_legacy_cache_never_blocks(self):
        self.plugin._tags_cache = {}
        for _ in range(4):
            await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 4)
        self.plugin._tags_cache = {"11111": {"slots": {}}}
        await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 5)

    async def test_risk_check_exception_fails_open(self):
        class Broken(dict):
            def get(self, key, default=None):
                if key == "slots":
                    raise RuntimeError("broken")
                return super().get(key, default)
        self.plugin._tags_cache = {"11111": Broken()}
        for _ in range(5):
            await self.query_self()
        self.assertEqual(self.plugin._send_profile.await_count, 5)
