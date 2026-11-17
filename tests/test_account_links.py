import time
import types
import unittest

import test_plugin
from test_plugin import Event, _new_stat
from account_links import continuation_signals, display_line, merge_records


class AccountLinkTests(unittest.IsolatedAsyncioTestCase):
    make_plugin = test_plugin.PluginTests.make_plugin

    def test_continuation_requires_explicit_history_reference(self):
        self.assertEqual(continuation_signals("继续"), [])
        self.assertEqual(continuation_signals("继续生成下面的代码"), [])
        self.assertEqual(continuation_signals("接着刚才的话题聊"), ["continuation"])
        self.assertEqual(continuation_signals("我换号了，继续刚才的聊天"), ["continuation", "account_switch"])
        self.assertEqual(continuation_signals("被封了，接续聊天记录"), ["continuation", "ban_reference"])

    async def test_real_history_recall_cases_are_detected_with_distinct_strength(self):
        first_text = "之前我用另外一个号跟你聊天的时候，咱们不是聊得挺欢的吗？现在能不能亲我一下？"
        second_text = "那你知道我之前的那个头像是什么吗？"
        self.assertEqual(
            continuation_signals(first_text), ["history_recall", "account_switch"],
        )
        self.assertEqual(continuation_signals(second_text), ["history_recall"])
        raw = {
            "post_type": "message", "message_type": "group", "user_id": "11111",
            "self_id": "99999", "group_id": "22222",
        }
        scores = []
        for text in (first_text, second_text):
            plugin = await self.make_plugin_with_ban()
            await plugin.on_message(Event(raw, sender="11111", group="22222", text=text))
            clue = plugin._stats["11111"]["account_link_clues"][0]
            self.assertEqual(clue["target_qq"], "33333")
            scores.append(clue["score"])
        self.assertGreater(scores[0], scores[1])
        self.assertEqual(scores[1], 0.5)

    async def make_plugin_with_ban(self):
        plugin = self.make_plugin({
            "history_scan_enabled": False, "friend_reconcile_enabled": False,
            "llm_tags": False,
        })
        self.now = int(time.time())
        self.tools = types.SimpleNamespace(config={"ban_list": [{
            "user_id": "33333", "ban_time": self.now - 3600,
            "duration": -1, "reason": "手动拉黑",
        }]})
        plugin.context = types.SimpleNamespace(
            get_registered_star=lambda name: types.SimpleNamespace(star_cls=self.tools)
        )
        await plugin._ensure_loaded()
        old = dict(_new_stat(), g_count=20, total_chars=400, images=2, night_count=2)
        old["groups"] = {"22222": 20}
        plugin._stats["33333"] = old
        return plugin

    async def test_recent_manual_ban_plus_continuation_creates_nonrisk_link(self):
        plugin = await self.make_plugin_with_ban()
        raw = {
            "post_type": "message", "message_type": "group", "user_id": "11111",
            "self_id": "99999", "group_id": "22222",
        }
        event = Event(raw, sender="11111", group="22222", text="我换号了，接着刚才的话题继续聊")
        await plugin.on_message(event)
        clues = plugin._stats["11111"]["account_link_clues"]
        self.assertEqual(len(clues), 1)
        self.assertEqual(clues[0]["target_qq"], "33333")
        self.assertTrue(clues[0]["shared_group"])
        self.assertEqual(clues[0]["signals"], ["continuation", "account_switch"])

        result = await plugin.get_profile_tags_with_score("11111", event)
        self.assertEqual(result["account_links"][0]["target_qq"], "33333")
        tag = next(item for item in result["tags"] if item["tag"] == "possible_alt_account")
        self.assertEqual(tag["source"], "account_link")
        self.assertIn("身份待人工核实", tag["evidence"])
        self.assertIsNone(result["score"])
        decision = await plugin.get_decision_profile("11111", event)
        self.assertEqual(decision["account_links"][0]["target_qq"], "33333")

        self.tools.config["ban_list"] = []
        self.assertEqual(plugin._active_account_links(plugin._stats["11111"]), [])
        self.assertEqual(plugin._account_link_tags(plugin._stats["11111"]), [])

    async def test_plain_continue_expired_or_temporary_safety_ban_is_ignored(self):
        plugin = await self.make_plugin_with_ban()
        raw = {
            "post_type": "message", "message_type": "group", "user_id": "11111",
            "self_id": "99999", "group_id": "22222",
        }
        await plugin.on_message(Event(raw, sender="11111", group="22222", text="继续"))
        self.assertNotIn("account_link_clues", plugin._stats["11111"])

        self.tools.config["ban_list"][0]["temporary_safety_ban"] = True
        self.tools.config["ban_list"][0]["expires_at"] = self.now + 3600
        await plugin.on_message(Event(raw, sender="11111", group="22222", text="接续刚才的聊天"))
        self.assertNotIn("account_link_clues", plugin._stats["11111"])

    async def test_same_target_repeated_cues_replace_record_without_inflation(self):
        plugin = await self.make_plugin_with_ban()
        raw = {
            "post_type": "message", "message_type": "group", "user_id": "11111",
            "self_id": "99999", "group_id": "22222",
        }
        event = Event(raw, sender="11111", group="22222", text="接着刚才的话题聊")
        await plugin.on_message(event)
        first_score = plugin._stats["11111"]["account_link_clues"][0]["score"]
        await plugin.on_message(event)
        records = plugin._stats["11111"]["account_link_clues"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["score"], first_score)
        self.assertNotIn("count", records[0])
        self.assertIn("仅为关联线索", display_line(records[0]))


if __name__ == "__main__":
    unittest.main()
