import copy
import time
import unittest
from unittest.mock import AsyncMock

import test_plugin
from main import _tag_visual_category


class ManagementResolutionTests(unittest.IsolatedAsyncioTestCase):
    make_plugin = test_plugin.PluginTests.make_plugin

    def plugin(self, config=None):
        return self.make_plugin({
            "history_scan_enabled": False,
            "llm_tags": False,
            "friend_reconcile_enabled": False,
            **(config or {}),
        })

    @staticmethod
    async def record(plugin, event_key, event_type, at, resolution=""):
        kwargs = dict(
            event_key=event_key, event_type=event_type, source="guard",
            event_time=at, event_created_at_ms=at * 1000,
        )
        if resolution:
            kwargs["resolution"] = resolution
        result = await plugin.record_management_event("11111", **kwargs)
        assert result["status"] == "recorded"

    async def test_normal_unban_decays_then_reaches_zero(self):
        now = int(time.time())
        plugin = self.plugin({"management_resolution_decay_days": 30})
        await self.record(plugin, "ban", "astrbot_banned", now - 20 * 86400)
        await self.record(
            plugin, "unban", "astrbot_unbanned", now - 15 * 86400,
            resolution="normal",
        )

        result = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(result["score"], 15)
        tag = next(item for item in result["tags"] if item["tag"] == "astrbot_banned")
        self.assertEqual(tag["confidence"], 0.5)
        self.assertIn("历史风险剩余 50%", tag["evidence"])

        state = await plugin.get_management_state("11111")
        self.assertEqual(state["unban_resolution"], "normal")
        self.assertEqual(
            state["risk_factors"]["astrbot_banned"]["status"], "decaying"
        )
        decision = await plugin.get_decision_profile("11111")
        self.assertEqual(decision["management"]["state"]["unban_resolution"], "normal")
        self.assertEqual(decision["management"]["recent"][-1]["resolution"], "normal")

        plugin._management_account("11111")["last_unban_time"] = now - 30 * 86400
        result = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(result["score"], 0)
        tag = next(item for item in result["tags"] if item["tag"] == "astrbot_banned")
        self.assertEqual(tag["confidence"], 0)
        self.assertIn("历史风险已归零", tag["evidence"])
        self.assertEqual(_tag_visual_category("astrbot_banned", 0), "行为")

    async def test_appeal_clears_ban_and_deleted_friend_but_keeps_audit(self):
        now = int(time.time())
        plugin = self.plugin()
        await self.record(plugin, "ban", "astrbot_banned", now - 100)
        await self.record(plugin, "delete", "bot_deleted_friend", now - 90)
        before = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(before["score"], 55)

        await self.record(
            plugin, "appeal", "astrbot_unbanned", now - 10,
            resolution="appeal_approved",
        )
        result = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(result["score"], 0)
        by_tag = {item["tag"]: item for item in result["tags"]}
        self.assertEqual(by_tag["astrbot_banned"]["confidence"], 0)
        self.assertEqual(by_tag["bot_deleted_friend"]["confidence"], 0)
        self.assertIn("申诉通过", by_tag["astrbot_banned"]["evidence"])
        self.assertIn("历史风险已归零", by_tag["bot_deleted_friend"]["evidence"])

        account = plugin._management_account("11111")
        self.assertEqual(account["counts"]["astrbot_banned"], 1)
        self.assertEqual(account["counts"]["astrbot_unbanned"], 1)
        self.assertEqual(account["counts"]["bot_deleted_friend"], 1)
        state = await plugin.get_management_state("11111")
        self.assertEqual(state["appeal_resolution"], "appeal_approved")
        self.assertEqual(
            state["risk_factors"]["bot_deleted_friend"]["status"], "appeal_cleared"
        )

        plugin._fetch_stranger_info = AsyncMock(return_value={})
        model = await plugin._build_profile_model("11111", None)
        self.assertTrue(model["has_record"])
        text = plugin._profile_model_to_text(model)
        self.assertIn("申诉通过", text)
        self.assertIn("历史风险已结清", text)

    async def test_repeat_ban_after_appeal_scores_full_again(self):
        now = int(time.time())
        plugin = self.plugin()
        await self.record(plugin, "ban:1", "astrbot_banned", now - 100)
        await self.record(
            plugin, "appeal", "astrbot_unbanned", now - 90,
            resolution="false_positive",
        )
        self.assertEqual((await plugin.get_profile_tags_with_score("11111"))["score"], 0)
        await self.record(plugin, "ban:2", "astrbot_banned", now - 10)
        result = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(result["score"], 30)
        tag = next(item for item in result["tags"] if item["tag"] == "astrbot_banned")
        self.assertEqual(tag["confidence"], 1)
        self.assertIn("尚未结清", tag["evidence"])

    async def test_friend_restore_decays_delete_history_and_appeal_can_disable(self):
        now = int(time.time())
        plugin = self.plugin({"management_resolution_decay_days": 30})
        await self.record(plugin, "delete", "bot_deleted_friend", now - 20 * 86400)
        await self.record(
            plugin, "restore", "friend_relation_restored", now - 15 * 86400
        )
        result = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(result["score"], 12)  # int(25 * 0.5)
        tag = next(item for item in result["tags"] if item["tag"] == "bot_deleted_friend")
        self.assertIn("好友恢复后历史风险剩余 50%", tag["evidence"])

        strict = self.plugin({
            "management_resolution_decay_days": 30,
            "management_appeal_clear_history": False,
        })
        await self.record(strict, "delete", "bot_deleted_friend", now - 20)
        await self.record(
            strict, "appeal", "astrbot_unbanned", now - 10,
            resolution="appeal_approved",
        )
        result = await strict.get_profile_tags_with_score("11111")
        self.assertEqual(result["score"], 25)
        strict._management_account("11111")["appeal_time"] = now - 31 * 86400
        result = await strict.get_profile_tags_with_score("11111")
        self.assertEqual(result["score"], 0)

    async def test_resolution_state_survives_truncation_restart_and_write_failure(self):
        now = int(time.time())
        plugin = self.plugin({"mute_event_keep": 1})
        await self.record(plugin, "ban", "astrbot_banned", now - 100)
        await self.record(
            plugin, "appeal", "astrbot_unbanned", now - 50,
            resolution="appeal_approved",
        )
        account = plugin._management_account("11111")
        self.assertEqual(len(account["events"]), 1)
        self.assertEqual(account["appeal_resolution"], "appeal_approved")
        self.assertEqual((await plugin.get_profile_tags_with_score("11111"))["score"], 0)

        restarted = self.plugin({"mute_event_keep": 1})
        restarted._kv = copy.deepcopy(plugin._kv)
        self.assertEqual((await restarted.get_profile_tags_with_score("11111"))["score"], 0)
        state = await restarted.get_management_state("11111")
        self.assertEqual(state["appeal_resolution"], "appeal_approved")

        before = copy.deepcopy(restarted._management_account("11111"))
        original_put = restarted.put_kv_data

        async def fail_once(key, value):
            if key == "up_management_events":
                raise RuntimeError("kv down")
            await original_put(key, value)

        restarted.put_kv_data = fail_once
        failed = await restarted.record_management_event(
            "11111", event_key="ban:retry", event_type="astrbot_banned",
            source="guard", event_time=now, event_created_at_ms=now * 1000,
        )
        self.assertEqual(failed["status"], "failure")
        self.assertEqual(restarted._management_account("11111"), before)

    async def test_legacy_management_accounts_are_conservative_or_derive_visible_resolution(self):
        now = int(time.time())
        plugin = self.plugin({"management_resolution_decay_days": 30})
        plugin._kv["up_management_events"] = {
            "11111": {
                "events": [], "seen": [],
                "counts": {"astrbot_banned": 2}, "last_time": now - 100,
            },
            "22222": {
                "events": [{
                    "event_key": "old-unban", "type": "astrbot_unbanned",
                    "source": "guard", "time": now - 15 * 86400,
                    "group_id": "", "reason": "旧普通解封",
                    "created_at_ms": (now - 15 * 86400) * 1000,
                }],
                "seen": ["old-unban"],
                "counts": {"astrbot_banned": 1, "astrbot_unbanned": 1},
                "last_time": now - 15 * 86400,
            },
        }
        count_only = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(count_only["score"], 30)
        tag = next(item for item in count_only["tags"] if item["tag"] == "astrbot_banned")
        self.assertIn("尚未结清", tag["evidence"])

        visible = await plugin.get_profile_tags_with_score("22222")
        self.assertEqual(visible["score"], 15)
        state = await plugin.get_management_state("22222")
        self.assertEqual(state["unban_resolution"], "normal")

    async def test_invalid_resolution_is_rejected_and_old_callers_still_work(self):
        plugin = self.plugin()
        invalid = await plugin.record_management_event(
            "11111", event_key="bad", event_type="astrbot_unbanned",
            source="guard", event_time=1, event_created_at_ms=1000,
            resolution="admin-says-so",
        )
        self.assertEqual(invalid["status"], "invalid")
        old_style = await plugin.record_management_event(
            "11111", event_key="ban", event_type="astrbot_banned",
            source="guard", event_time=1, event_created_at_ms=1000,
        )
        self.assertEqual(old_style["status"], "recorded")


if __name__ == "__main__":
    unittest.main()
