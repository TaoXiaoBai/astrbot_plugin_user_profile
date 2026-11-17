import copy
import time
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_plugin
from test_plugin import Event, _new_stat


class BannedMemoryCleanupTests(unittest.IsolatedAsyncioTestCase):
    make_plugin = test_plugin.PluginTests.make_plugin

    def setUp(self):
        self.now = int(time.time())

    async def make_case(self, ban=None, config=None):
        plugin = self.make_plugin({
            "history_scan_enabled": False, "friend_reconcile_enabled": False,
            "banned_memory_cleanup_enabled": True,
            "banned_memory_retention_days": 30,
            "banned_memory_cleanup_interval": 60,
            **(config or {}),
        })
        self.tools = types.SimpleNamespace(config={"ban_list": [ban] if ban else []})
        plugin.context = types.SimpleNamespace(
            get_registered_star=lambda name: types.SimpleNamespace(star_cls=self.tools)
        )
        await plugin._ensure_loaded()
        plugin._stats["11111"] = dict(_new_stat(), g_count=8)
        plugin._quotes["11111"] = [{"t": self.now, "src": "群 22222", "text": "旧原话"}]
        plugin._tags_cache["11111"] = {"slots": {"group": {"long_term_traits": [{"trait": "旧记忆"}]}}}
        plugin._mute_events["11111"] = {"events": []}
        plugin._management_events["11111"] = {"events": []}
        for key in ("_dirty", "_tags_dirty", "_mute_events_dirty", "_management_events_dirty"):
            setattr(plugin, key, False)
        return plugin

    def permanent_ban(self, days=31):
        return {
            "user_id": "11111", "ban_time": self.now - days * 86400,
            "duration": -1, "reason": "测试长期拉黑",
        }

    async def test_long_ban_safely_purges_memory_without_recreating_until_unban(self):
        plugin = await self.make_case(self.permanent_ban())
        original_bans = copy.deepcopy(self.tools.config["ban_list"])
        plugin._cancel_guard_profile_deliveries = AsyncMock()

        deleted = await plugin._cleanup_long_banned_memories()
        self.assertEqual(deleted, ["11111"])
        for store in (plugin._stats, plugin._quotes, plugin._tags_cache,
                      plugin._mute_events, plugin._management_events):
            self.assertNotIn("11111", store)
        self.assertIn("11111", plugin._mute_delete_cutoffs)
        self.assertEqual(self.tools.config["ban_list"], original_bans)
        plugin._cancel_guard_profile_deliveries.assert_awaited_once_with("11111")
        await plugin._flush()
        self.assertNotIn("11111", plugin._kv["up_stats"])
        self.assertNotIn("11111", plugin._kv["up_tags"])
        self.assertIn("11111", plugin._kv["up_bot_mute_delete_cutoffs"])

        raw = {
            "post_type": "message", "message_type": "group", "user_id": "11111",
            "self_id": "99999", "group_id": "22222",
        }
        event = Event(raw, sender="11111", group="22222", text="拉黑期间的新话")
        await plugin.on_message(event)
        self.assertNotIn("11111", plugin._stats)
        self.assertEqual(await plugin.get_profile_tags("11111", event), [])
        blocked = await plugin.record_management_event(
            "11111", event_key="late:ban", event_type="astrbot_banned",
            source="test", event_time=self.now,
        )
        self.assertEqual(blocked["status"], "long_banned_deleted")
        self.assertNotIn("11111", plugin._management_events)

        self.tools.config["ban_list"] = []
        await plugin.on_message(event)
        self.assertEqual(plugin._stats["11111"]["g_count"], 1)
        self.assertNotIn("11111", plugin._tags_cache)

    async def test_short_expired_invalid_or_disabled_ban_never_deletes(self):
        cases = (
            self.permanent_ban(29),
            dict(self.permanent_ban(40), duration=1),
            dict(self.permanent_ban(40), ban_time=0),
            None,
        )
        for ban in cases:
            with self.subTest(ban=ban):
                plugin = await self.make_case(ban)
                self.assertEqual(await plugin._cleanup_long_banned_memories(), [])
                self.assertIn("11111", plugin._stats)

        plugin = await self.make_case(
            self.permanent_ban(), {"banned_memory_cleanup_enabled": False},
        )
        self.assertEqual(await plugin._cleanup_long_banned_memories(), [])
        self.assertIn("11111", plugin._stats)

    async def test_unavailable_deletion_cutoffs_fail_closed(self):
        plugin = await self.make_case(self.permanent_ban())
        plugin._mute_delete_cutoffs = None

        async def fail_cutoffs(key, default):
            if key == "up_bot_mute_delete_cutoffs":
                raise OSError("temporary read failure")
            return copy.deepcopy(plugin._kv.get(key, default))

        plugin.get_kv_data = fail_cutoffs
        self.assertEqual(await plugin._cleanup_long_banned_memories(), [])
        self.assertIn("11111", plugin._stats)
        self.assertIn("11111", plugin._tags_cache)

    async def test_regular_flush_runs_cleanup_even_without_other_dirty_data(self):
        plugin = await self.make_case(self.permanent_ban())
        self.assertFalse(plugin._dirty)
        with patch("main.time.monotonic", return_value=10000):
            await plugin._flush()
        self.assertNotIn("11111", plugin._stats)
        self.assertNotIn("11111", plugin._kv["up_stats"])


if __name__ == "__main__":
    unittest.main()
