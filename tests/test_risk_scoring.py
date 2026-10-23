import copy
import types
import unittest
from unittest.mock import AsyncMock

import test_plugin
from test_plugin import _new_stat
from risk_scoring import (
    DEFAULT_WEIGHTS,
    calculate_mute_components,
    calculate_tag_components,
    calculate_tag_score,
    merged_weights,
    risk_level,
)
from scene_profiles import policy


def tag(name, confidence=1):
    return {"tag": name, "confidence": confidence}


class RiskScoringTests(unittest.TestCase):
    def test_zero_baseline_neutral_tags_and_hard_evidence(self):
        self.assertEqual(calculate_tag_score([]), 0)
        neutral = [
            "invite_rejected", "kick_history", "frequent_inviter", "newcomer",
            "private_active", "night_active", "active_high", "active_medium",
            "friendly", "helpful", "normal",
        ]
        self.assertEqual(calculate_tag_score([tag(name) for name in neutral]), 0)
        self.assertEqual(
            calculate_tag_score([
                tag("ban_history"), tag("friendly"), tag("helpful"), tag("normal"),
            ]),
            40,
        )

    def test_duplicate_confidence_and_category_caps(self):
        components = calculate_tag_components([
            tag("scam_suspect", 0.2), tag("scam_suspect"), tag("ad_suspect"),
            tag("spam_suspect"), tag("troll"), tag("nsfw_tendency"),
            tag("political_sensitive"), tag("qr_spammer"), tag("link_spammer"),
            tag("image_spammer"),
        ])
        self.assertEqual(components, {"verified": 0, "semantic": 60, "behavior": 15})
        self.assertEqual(
            calculate_tag_components([tag("ban_history", 0.2), tag("ban_history")]),
            {"verified": 40, "semantic": 0, "behavior": 0},
        )
        self.assertEqual(
            calculate_tag_components(
                [tag("scam_suspect"), tag("qr_spammer")],
                semantic_cap=10, behavior_cap=5,
            ),
            {"verified": 0, "semantic": 10, "behavior": 5},
        )

    def test_custom_weights_are_finite_non_negative_and_allowlisted(self):
        weights = merged_weights({
            "ban_history": -50,
            "friendly": -10,
            "scam_suspect": "bad",
            "ad_suspect": float("nan"),
            "spam_suspect": True,
            "troll": "12.5",
            "unknown": 999,
        })
        self.assertEqual(weights["ban_history"], 0)
        self.assertEqual(weights["friendly"], 0)
        self.assertEqual(weights["scam_suspect"], DEFAULT_WEIGHTS["scam_suspect"])
        self.assertEqual(weights["ad_suspect"], DEFAULT_WEIGHTS["ad_suspect"])
        self.assertEqual(weights["spam_suspect"], DEFAULT_WEIGHTS["spam_suspect"])
        self.assertEqual(weights["troll"], 12.5)
        self.assertNotIn("unknown", weights)

    def test_unknown_is_distinct_from_zero(self):
        config = {"risk_level_low": 30, "risk_level_high": 60, "risk_level_extreme": 80}
        self.assertEqual(risk_level(None, config), "未知")
        self.assertEqual(risk_level(True, config), "未知")
        self.assertEqual(risk_level(0, config), "低")
        self.assertEqual(risk_level(80, config), "极高")

    def test_mute_roles_and_legacy_aggregate_migration(self):
        self.assertEqual(
            calculate_mute_components({
                "risk_schema_version": 2,
                "operator_risk_total": 90,
                "associated_risk_total": 40,
            }),
            {"mute_operator": 70, "mute_associated": 20},
        )
        self.assertEqual(
            calculate_mute_components({
                "risk_total": 50, "operator_count": 0, "associated_count": 3,
            }),
            {"mute_operator": 0, "mute_associated": 20},
        )
        self.assertEqual(
            calculate_mute_components({
                "risk_total": 90, "operator_count": 1, "associated_count": 2,
            }),
            {"mute_operator": 70, "mute_associated": 0},
        )
        self.assertEqual(
            calculate_mute_components({
                "risk_schema_version": 2, "legacy_risk_total": 40,
                "legacy_has_operator": True, "operator_risk_total": 10,
                "associated_risk_total": 30,
            }),
            {"mute_operator": 50, "mute_associated": 20},
        )


class RiskIntegrationTests(unittest.IsolatedAsyncioTestCase):
    make_plugin = test_plugin.PluginTests.make_plugin

    def risk_plugin(self, config=None):
        return self.make_plugin({
            "history_scan_enabled": False,
            "llm_tags": False,
            "friend_reconcile_enabled": False,
            **(config or {}),
        })

    @staticmethod
    def install_guard_caps(plugin, operator=70, associated=20):
        values = {
            "profile_mute_risk_cap": operator,
            "profile_mute_associated_risk_cap": associated,
        }
        guard = types.SimpleNamespace(
            _cfg=lambda group, key, default: values.get(key, default)
        )
        plugin.context = types.SimpleNamespace(
            get_registered_star=lambda name: types.SimpleNamespace(star_cls=guard)
        )

    async def test_empty_friendly_and_blacklist_profiles_are_distinct(self):
        plugin = self.risk_plugin()
        empty = await plugin.get_profile_tags_with_score("11111")
        self.assertIsNone(empty["score"])
        self.assertEqual(empty["level"], "未知")
        self.assertEqual(empty["risk_status"], "no_evidence")
        self.assertIsNone(await plugin.get_risk_score("11111"))
        self.assertEqual(await plugin.get_decision_profile("11111"), {})

        await plugin._ensure_loaded()
        plugin._stats["11111"] = dict(_new_stat(), g_count=1)
        plugin._tags_cache["11111"] = {"slots": {"group": {
            "status": "success", "time": 1, "policy": list(policy(plugin)),
            "tags": [{"tag": "friendly", "confidence": 1, "reason": "礼貌"}],
        }}}
        friendly = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(friendly["score"], 0)
        self.assertEqual(friendly["risk_status"], "ready")
        self.assertIn("friendly", [item["tag"] for item in friendly["tags"]])
        decision = await plugin.get_decision_profile("11111")
        self.assertEqual(decision["score"], 0)
        self.assertEqual(decision["risk_status"], "ready")

        plugin._load_ban_entry = AsyncMock(return_value=["当前位于 bot 黑名单"])
        banned = await plugin.get_profile_tags_with_score("11111")
        self.assertEqual(banned["score"], 38)
        self.assertEqual(banned["risk_components"]["verified"], 38)

    async def test_small_samples_do_not_create_behavior_labels(self):
        plugin = self.risk_plugin()
        st = _new_stat()
        st.update({
            "g_count": 1, "images": 1, "links": 1, "qrs": 1,
            "mentions": 1, "total_chars": 200, "night_count": 1,
        })
        tags = plugin._tag_engine.generate_base_tags("11111", st, [], {}, [])
        names = {item["tag"] for item in tags}
        self.assertTrue(names.isdisjoint({
            "image_spammer", "link_spammer", "qr_spammer", "mention_heavy",
            "verbose", "night_active",
        }))

        st.update({
            "g_count": 10, "images": 10, "links": 10, "qrs": 2,
            "mentions": 10, "total_chars": 1000, "night_count": 10,
        })
        tags = plugin._tag_engine.generate_base_tags("11111", st, [], {}, [])
        by_name = {item["tag"]: item for item in tags}
        self.assertTrue({
            "image_spammer", "link_spammer", "qr_spammer", "mention_heavy",
            "verbose", "night_active",
        }.issubset(by_name))
        self.assertIn("样本 10 条", by_name["qr_spammer"]["evidence"])

    async def test_legacy_dict_and_list_ledgers_keep_score_without_inventing_roles(self):
        plugin = self.risk_plugin()
        plugin._kv["up_bot_mute_events"] = {
            "22222": {
                "events": [], "seen": [], "risk_total": 50,
                "operator_count": 0, "associated_count": 3, "last_risk_cap": 70,
            },
            "33333": {
                "events": [], "seen": [], "risk_total": 90,
                "operator_count": 1, "associated_count": 2, "last_risk_cap": 70,
            },
            "44444": [
                {
                    "event_key": "stable:direct", "group_id": "30000", "time": 1,
                    "operator_id": "44444", "inviter_id": "",
                    "attribution": "operator", "risk_increment": 35, "risk_cap": 70,
                },
                {
                    "event_key": "stable:associated", "group_id": "30000", "time": 2,
                    "operator_id": "55555", "inviter_id": "44444",
                    "attribution": "associated_inviter", "risk_increment": 10, "risk_cap": 20,
                },
            ],
            "66666": {
                "events": [], "seen": [], "risk_total": 15,
                "operator_count": 0, "associated_count": 0, "last_risk_cap": 70,
            },
        }
        self.install_guard_caps(plugin)
        await plugin._ensure_loaded()
        pure_associated = await plugin.get_profile_tags_with_score("22222")
        mixed_legacy = await plugin.get_profile_tags_with_score("33333")
        old_list = await plugin.get_profile_tags_with_score("44444")
        aggregate_only = await plugin.get_profile_tags_with_score("66666")
        self.assertEqual(pure_associated["score"], 20)
        self.assertEqual(
            [item["tag"] for item in pure_associated["tags"]],
            ["bot_mute_associated"],
        )
        self.assertEqual(
            pure_associated["risk_components"],
            {"verified": 0, "semantic": 0, "behavior": 0,
             "mute_operator": 0, "mute_associated": 20},
        )
        self.assertEqual(mixed_legacy["score"], 70)
        self.assertEqual(mixed_legacy["risk_components"]["mute_associated"], 0)
        self.assertEqual(old_list["score"], 45)
        self.assertEqual(old_list["risk_components"]["mute_operator"], 35)
        self.assertEqual(old_list["risk_components"]["mute_associated"], 10)
        self.assertEqual(aggregate_only["score"], 15)
        self.assertEqual(aggregate_only["tags"], [])

        decision = await plugin.get_decision_profile("66666")
        self.assertEqual(decision["score"], 15)
        self.assertEqual(decision["schema_version"], 4)
        plugin._fetch_stranger_info = AsyncMock(return_value={})
        model = await plugin._build_profile_model("66666", None)
        self.assertTrue(model["has_record"])
        self.assertIn("关联禁言 15", plugin._profile_model_to_text(model))

    async def test_role_totals_survive_display_truncation_and_restart(self):
        plugin = self.risk_plugin({"mute_event_keep": 1})
        self.install_guard_caps(plugin)
        direct = dict(
            group_id="30000", event_time=1, duration=60,
            operator_id="22222", inviter_id="", attribution="operator",
            risk_increment=35, risk_cap=70, self_id="99999",
        )
        associated = dict(
            group_id="30000", event_time=2, duration=60,
            operator_id="33333", inviter_id="22222",
            attribution="associated_inviter", risk_increment=10, risk_cap=20,
            self_id="99999",
        )
        self.assertEqual((await plugin.record_bot_mute_event(
            "22222", event_key="stable:direct", **direct
        ))["status"], "recorded")
        self.assertEqual((await plugin.record_bot_mute_event(
            "22222", event_key="stable:associated", **associated
        ))["status"], "recorded")
        self.assertEqual(len(plugin._personal_mute_events("22222")), 1)
        self.assertEqual(plugin._mute_event_risk("22222"), 45)

        restarted = self.risk_plugin({"mute_event_keep": 1})
        restarted._kv = copy.deepcopy(plugin._kv)
        self.install_guard_caps(restarted)
        self.assertEqual((await restarted.record_bot_mute_event(
            "22222", event_key="stable:direct", **direct
        ))["status"], "duplicate")
        result = await restarted.get_profile_tags_with_score("22222")
        self.assertEqual(result["score"], 45)
        self.assertEqual(result["risk_components"]["mute_operator"], 35)
        self.assertEqual(result["risk_components"]["mute_associated"], 10)

    async def test_failed_write_restores_nested_role_totals_and_seen(self):
        plugin = self.risk_plugin()
        kwargs = dict(
            group_id="30000", event_time=1, duration=60,
            operator_id="22222", self_id="99999", risk_increment=35, risk_cap=70,
        )
        self.assertEqual((await plugin.record_bot_mute_event(
            "22222", event_key="stable:first", **kwargs
        ))["status"], "recorded")
        before = copy.deepcopy(plugin._personal_mute_account("22222"))
        original_put = plugin.put_kv_data

        async def fail_once(key, value):
            if key == "up_bot_mute_events":
                raise RuntimeError("kv down")
            await original_put(key, value)

        plugin.put_kv_data = fail_once
        self.assertEqual((await plugin.record_bot_mute_event(
            "22222", event_key="stable:retry", **kwargs
        ))["status"], "failure")
        self.assertEqual(plugin._personal_mute_account("22222"), before)
        plugin.put_kv_data = original_put
        self.assertEqual((await plugin.record_bot_mute_event(
            "22222", event_key="stable:retry", **kwargs
        ))["status"], "recorded")
        self.assertEqual(plugin._mute_event_risk("22222"), 70)


if __name__ == "__main__":
    unittest.main()
