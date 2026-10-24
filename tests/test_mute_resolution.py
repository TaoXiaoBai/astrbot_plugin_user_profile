import copy
import time
import unittest

import test_plugin as _test_plugin
from risk_scoring import calculate_mute_components


class MuteResolutionTests(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self, config=None):
        return _test_plugin.PluginTests.make_plugin(self, {
            "history_scan_enabled": False,
            "llm_tags": False,
            "friend_reconcile_enabled": False,
            **(config or {}),
        })

    @staticmethod
    async def mute(plugin, key, at, increment=80):
        result = await plugin.record_bot_mute_event(
            "11111", event_key=key, group_id="30000", event_time=at,
            duration=60, operator_id="11111", attribution="operator",
            risk_increment=increment, risk_cap=100, self_id="99999",
        )
        assert result["status"] == "recorded"

    @staticmethod
    async def management(plugin, key, event_type, at, resolution=""):
        kwargs = dict(
            event_key=key, event_type=event_type, source="guard",
            event_time=at, event_created_at_ms=at * 1000,
        )
        if resolution:
            kwargs["resolution"] = resolution
        result = await plugin.record_management_event("11111", **kwargs)
        assert result["status"] == "recorded"

    def test_role_offsets_are_bounded_and_legacy_totals_never_negative(self):
        account = {
            "risk_schema_version": 2,
            "legacy_risk_total": 50, "legacy_has_operator": True,
            "operator_risk_total": 40, "associated_risk_total": 30,
        }
        self.assertEqual(
            calculate_mute_components(account, 100, 100),
            {"mute_operator": 90, "mute_associated": 30},
        )
        self.assertEqual(
            calculate_mute_components(
                account, 100, 100,
                resolution_effects={"mute_operator": 70, "mute_associated": 50},
                resolution_factor=0.5,
            ),
            {"mute_operator": 55, "mute_associated": 5},
        )
        self.assertEqual(
            calculate_mute_components(
                account, 100, 100,
                resolution_effects={"mute_operator": 1000, "mute_associated": 1000},
                resolution_factor=0,
            ),
            {"mute_operator": 0, "mute_associated": 0},
        )

    async def test_admin_like_80_mute_plus_25_delete_is_cleared_by_appeal(self):
        now = int(time.time())
        plugin = self.make_plugin()
        await self.mute(plugin, "stable:mute", now - 100, 80)
        await self.management(plugin, "delete", "bot_deleted_friend", now - 100)
        before = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(before["score"], 100)
        self.assertEqual(before["risk_components"]["mute_operator"], 80)
        self.assertEqual(before["risk_components"]["verified"], 25)

        await self.management(
            plugin, "appeal", "astrbot_unbanned", now - 10,
            resolution="appeal_approved",
        )
        result = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["risk_components"]["mute_operator"], 0)
        self.assertEqual(result["risk_components"]["verified"], 0)
        self.assertEqual(len(plugin._personal_mute_events("11111")), 1)
        self.assertEqual(plugin._personal_mute_account("11111")["risk_total"], 80)
        snapshot = plugin._management_account("11111")["appeal_mute_resolution"]
        self.assertEqual(snapshot["effects"]["mute_operator"], 80)
        self.assertEqual(snapshot["event_keys"], ["stable:mute"])
        state = await plugin.get_management_state("11111")
        self.assertEqual(state["mute_resolution"]["factor"], 0)
        self.assertEqual(state["mute_resolution"]["event_count"], 1)
        mute_tag = next(item for item in result["tags"] if item["tag"] == "bot_mute_operator")
        self.assertEqual(mute_tag["confidence"], 0)
        self.assertIn("已冲销快照内旧风险", mute_tag["evidence"])

    async def test_normal_unban_decays_mute_snapshot_and_new_mute_still_scores(self):
        now = int(time.time())
        plugin = self.make_plugin({"management_resolution_decay_days": 30})
        await self.mute(plugin, "old", now - 20 * 86400, 80)
        await self.management(
            plugin, "normal", "astrbot_unbanned", now - 15 * 86400,
            resolution="normal",
        )
        result = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(result["score"], 40)
        self.assertEqual(result["risk_components"]["mute_operator"], 40)

        await self.management(
            plugin, "appeal", "astrbot_unbanned", now - 10 * 86400,
            resolution="appeal_approved",
        )
        self.assertEqual((await plugin.get_profile_tags_with_score("11111"))["score"], 0)
        await self.mute(plugin, "new", now - 5 * 86400, 35)
        result = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(result["score"], 35)
        self.assertEqual(result["risk_components"]["mute_operator"], 35)

        # 后来的普通解封不能把已经由申诉结清的旧 80 分重新带回来。
        await self.management(
            plugin, "normal-later", "astrbot_unbanned", now - 86400,
            resolution="normal",
        )
        result = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(result["risk_components"]["mute_operator"], 35)

    async def test_snapshot_survives_management_truncation_restart_and_failed_write(self):
        now = int(time.time())
        plugin = self.make_plugin({"mute_event_keep": 1})
        await self.mute(plugin, "old", now - 100, 80)
        await self.management(plugin, "delete", "bot_deleted_friend", now - 90)
        await self.management(
            plugin, "appeal", "astrbot_unbanned", now - 50,
            resolution="false_positive",
        )
        self.assertEqual(len(plugin._management_account("11111")["events"]), 1)
        self.assertEqual((await plugin.get_profile_tags_with_score("11111"))["score"], 0)

        restarted = self.make_plugin({"mute_event_keep": 1})
        restarted._kv = copy.deepcopy(plugin._kv)
        self.assertEqual((await restarted.get_profile_tags_with_score("11111"))["score"], 0)
        before = copy.deepcopy(restarted._management_account("11111"))
        original_put = restarted.put_kv_data

        async def fail(key, value):
            if key == "up_management_events":
                raise RuntimeError("kv down")
            await original_put(key, value)

        restarted.put_kv_data = fail
        failed = await restarted.record_management_event(
            "11111", event_key="retry", event_type="astrbot_unbanned",
            source="guard", event_time=now, event_created_at_ms=now * 1000,
            resolution="normal",
        )
        self.assertEqual(failed["status"], "failure")
        self.assertEqual(restarted._management_account("11111"), before)

    @staticmethod
    def without_mute_snapshots(plugin, truncate_management=False, empty_snapshots=False):
        legacy = copy.deepcopy(plugin._kv)
        account = legacy["up_management_events"]["11111"]
        account.pop("mute_resolution", None)
        account.pop("appeal_mute_resolution", None)
        for item in account["events"]:
            item.pop("mute_resolution", None)
            if empty_snapshots and item["type"] == "astrbot_unbanned":
                item["mute_resolution"] = {
                    "time": item["time"], "resolution": item["resolution"],
                    "effects": {"mute_operator": 0, "mute_associated": 0},
                    "event_keys": [],
                }
        if empty_snapshots:
            for field, time_key, resolution_key in (
                ("mute_resolution", "last_unban_time", "unban_resolution"),
                ("appeal_mute_resolution", "appeal_time", "appeal_resolution"),
            ):
                if account.get(time_key):
                    account[field] = {
                        "time": account[time_key], "resolution": account[resolution_key],
                        "effects": {"mute_operator": 0, "mute_associated": 0},
                        "event_keys": [],
                    }
        if truncate_management:
            account["events"] = []
        return legacy

    async def test_old_unban_backfills_only_mutes_already_existing_then(self):
        now = int(time.time())
        plugin = self.make_plugin({"management_resolution_decay_days": 30})
        await self.mute(plugin, "old", now - 20 * 86400, 80)
        await self.management(
            plugin, "normal", "astrbot_unbanned", now - 15 * 86400,
            resolution="normal",
        )
        await self.mute(plugin, "new", now - 10 * 86400, 35)

        restarted = self.make_plugin({"management_resolution_decay_days": 30})
        restarted._kv = self.without_mute_snapshots(plugin)
        result = await restarted.get_profile_tags_with_score("11111")
        self.assertEqual(result["score"], 75)
        snapshot = restarted._management_account("11111")["mute_resolution"]
        self.assertEqual(snapshot["effects"]["mute_operator"], 80)
        self.assertEqual(snapshot["event_keys"], ["old"])
        self.assertTrue(restarted._management_events_dirty)

        await restarted._flush()
        self.assertFalse(restarted._management_events_dirty)
        third = self.make_plugin({"management_resolution_decay_days": 30})
        third._kv = copy.deepcopy(restarted._kv)
        self.assertEqual((await third.get_profile_tags_with_score("11111"))["score"], 75)
        self.assertFalse(third._management_events_dirty)

    async def test_v1161_empty_placeholder_is_rebuilt_once(self):
        now = int(time.time())
        plugin = self.make_plugin()
        await self.mute(plugin, "old", now - 100, 80)
        await self.management(
            plugin, "appeal", "astrbot_unbanned", now - 50,
            resolution="appeal_approved",
        )
        restarted = self.make_plugin()
        restarted._kv = self.without_mute_snapshots(plugin, empty_snapshots=True)
        self.assertEqual((await restarted.get_profile_tags_with_score("11111"))["score"], 0)
        snapshot = restarted._management_account("11111")["appeal_mute_resolution"]
        self.assertEqual(snapshot["effects"]["mute_operator"], 80)
        self.assertEqual(snapshot["event_keys"], ["old"])

        await restarted._flush()
        third = self.make_plugin()
        third._kv = copy.deepcopy(restarted._kv)
        self.assertEqual((await third.get_profile_tags_with_score("11111"))["score"], 0)
        self.assertFalse(third._management_events_dirty)

    async def test_truncated_old_management_events_backfill_top_level_appeal(self):
        now = int(time.time())
        plugin = self.make_plugin()
        await self.mute(plugin, "old", now - 100, 80)
        await self.management(plugin, "delete", "bot_deleted_friend", now - 90)
        await self.management(
            plugin, "appeal", "astrbot_unbanned", now - 50,
            resolution="appeal_approved",
        )

        restarted = self.make_plugin()
        restarted._kv = self.without_mute_snapshots(plugin, truncate_management=True)
        result = await restarted.get_profile_tags_with_score("11111")
        self.assertEqual(result["score"], 0)
        state = await restarted.get_management_state("11111")
        self.assertEqual(state["mute_resolution"]["event_count"], 1)
        self.assertEqual(state["mute_resolution"]["factor"], 0)
        self.assertEqual(restarted._personal_mute_account("11111")["risk_total"], 80)

    async def test_legacy_aggregate_without_snapshot_stays_conservative(self):
        now = int(time.time())
        plugin = self.make_plugin()
        await plugin._ensure_loaded()
        legacy_mute = {
            "events": [], "seen": [], "risk_total": 80,
            "risk_schema_version": 2, "legacy_risk_total": 80,
            "legacy_has_operator": True,
            "operator_risk_total": 0, "associated_risk_total": 0,
            "operator_count": 1, "associated_count": 0,
            "last_risk_cap": 100, "last_time": now - 100,
        }
        plugin._mute_events["11111"] = copy.deepcopy(legacy_mute)
        plugin._kv.setdefault("up_bot_mute_events", {})["11111"] = copy.deepcopy(legacy_mute)
        await self.management(
            plugin, "appeal", "astrbot_unbanned", now - 50,
            resolution="appeal_approved",
        )
        result = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(result["score"], 80)
        snapshot = plugin._management_account("11111")["appeal_mute_resolution"]
        self.assertEqual(snapshot["effects"]["mute_operator"], 0)

        restarted = self.make_plugin()
        restarted._kv = self.without_mute_snapshots(plugin)
        self.assertEqual((await restarted.get_profile_tags_with_score("11111"))["score"], 80)


if __name__ == "__main__":
    unittest.main()
