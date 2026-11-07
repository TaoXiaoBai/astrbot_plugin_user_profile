import copy
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import test_plugin
from test_plugin import Event, _new_stat


class OutputVisibilityTests(unittest.IsolatedAsyncioTestCase):
    make_plugin = test_plugin.PluginTests.make_plugin

    @staticmethod
    def audience(admin=False, group="22222", raw_type=None):
        event = Event({"message_type": raw_type or ("group" if group else "private")}, group=group)
        event.is_admin = lambda: admin
        return event

    async def prepared(self, **config):
        plugin = self.make_plugin({"history_scan_enabled": False, "friend_reconcile_enabled": False, **config})
        await plugin._ensure_loaded()
        plugin._stats["11111"] = dict(_new_stat(), g_count=2)
        plugin._fetch_stranger_info = AsyncMock(return_value={})
        plugin._prepare_avatar_bytes = AsyncMock(return_value=None)
        plugin._load_ban_entry = AsyncMock(return_value=[])
        plugin._load_invite_guard_records = AsyncMock(return_value=[])
        result = {
            "score": 36, "level": "中", "risk_status": "ready", "risk_scope": "group",
            "scene": "group", "visibility": "group", "sources": ["INTERNAL_SOURCE"],
            "review_level": "中等", "review_pending": False,
            "risk_components": {"semantic": 36},
            "tags": [{"tag": "nsfw_tendency", "confidence": 0.9, "source": "INTERNAL_SOURCE",
                      "severity": "clear", "evidence": "INTERNAL_EVIDENCE"}],
            "impression": "表达稳定", "traits": ["热心"],
            "long_term_traits": [{
                "trait": "稳定偏好", "confidence": 0.9, "reason": "INTERNAL_LONG_REASON",
                "stability": "established", "evidence_refs": ["r_INTERNAL_SECRET"],
                "evidence_days": [1], "scenes": ["private"], "first_evidence_at": 1,
            }],
            "sections": {},
        }
        plugin.get_profile_tags_with_score = AsyncMock(return_value=result)
        return plugin, result

    def assert_public_text(self, text):
        for forbidden in ("审查：", "已按本档审查", "待复核", "内容关注", "风险构成", "INTERNAL_SOURCE", "INTERNAL_EVIDENCE", "来源 ", "review_level", "review_pending", "risk_components"):
            self.assertNotIn(forbidden, text)

    def assert_public_projection(self, result):
        for key in ("review_level", "review_pending", "risk_components", "source", "sources", "evidence"):
            self.assertNotIn(key, result)
        for tag in result.get("tags", []):
            for key in ("source", "evidence", "severity", "content_risk"):
                self.assertNotIn(key, tag)
        for trait in result.get("long_term_traits", []):
            for key in ("evidence_refs", "evidence_days", "scenes", "first_evidence_at", "last_evidence_at"):
                self.assertNotIn(key, trait)
        self.assertEqual(result["score"], 36)

    async def test_projection_audience_matrix_and_original_not_mutated(self):
        plugin, result = await self.prepared()
        original = copy.deepcopy(result)
        for admin, group in ((False, "22222"), (True, "22222"), (False, ""), (True, "")):
            with self.subTest(admin=admin, group=group):
                event = self.audience(admin, group)
                self.assertEqual(plugin._can_show_audit_details(event), admin and not group)
                view = plugin._public_profile_view(result, event)
                if admin and not group:
                    self.assertEqual(view["review_level"], "中等")
                    self.assertEqual(view["tags"][0]["evidence"], "INTERNAL_EVIDENCE")
                else:
                    self.assert_public_projection(view)
                self.assertEqual(result, original)
        self.assert_public_projection(plugin._public_profile_view(result, None))

    async def test_model_text_tool_audience_matrix(self):
        plugin, _ = await self.prepared()
        for admin, group in ((False, "22222"), (True, "22222"), (False, ""), (True, "")):
            with self.subTest(admin=admin, group=group):
                event = self.audience(admin, group)
                model = await plugin._build_profile_model("11111", event)
                text = plugin._profile_model_to_text(model)
                tool = await plugin.user_profile_query(event, "11111")
                self.assertEqual(model["audit_details"], admin and not group)
                self.assertEqual(model["score"], 36)
                for output in (text, tool):
                    self.assertIn("36", output)
                    self.assertIn("不适宜内容", output)
                    if admin and not group:
                        self.assertIn("审查：中等", output)
                        self.assertIn("INTERNAL_EVIDENCE", output)
                    else:
                        self.assert_public_text(output)
                if not (admin and not group):
                    self.assert_public_projection(model)

    async def test_review_lines_fail_closed_without_explicit_audit_flag(self):
        plugin, _ = await self.prepared()
        model = {"review_level": "高敏感", "review_pending": True,
                 "tags": [{"content_risk": True, "evidence": "INTERNAL_EVIDENCE"}]}
        self.assertEqual(plugin._review_lines(model), [])
        model["audit_details"] = False
        self.assertEqual(plugin._review_lines(model), [])
        model["audit_details"] = True
        self.assertIn("高敏感", "\n".join(plugin._review_lines(model)))

    async def test_admin_must_be_exact_true_and_sender_must_be_valid(self):
        plugin, _ = await self.prepared()
        for value in (1, "true", "admin", None, False):
            event = self.audience(True, "")
            event.is_admin = lambda: value
            self.assertFalse(plugin._can_show_audit_details(event), repr(value))
        for sender in ("", "not-a-user", "1234", "1234567890123"):
            event = self.audience(True, "")
            event._sender = sender
            self.assertFalse(plugin._can_show_audit_details(event), repr(sender))

    async def test_admin_identity_errors_and_ambiguous_message_type_fail_closed(self):
        plugin, _ = await self.prepared()
        def broken():
            raise RuntimeError("identity unavailable")
        for method in ("get_sender_id", "get_group_id", "is_admin"):
            event = self.audience(True, "")
            setattr(event, method, broken)
            self.assertFalse(plugin._can_show_audit_details(event), method)
        for raw in ({}, {"message_type": "group"}, {"message_type": "notice"}):
            event = self.audience(True, "")
            event.message_obj.raw_message = raw
            self.assertFalse(plugin._can_show_audit_details(event), raw)
        self.assertFalse(plugin._can_show_audit_details(None))
        self.assertFalse(plugin._can_show_audit_details(self.audience(True, "22222", "private")))

    async def test_framework_private_message_enum_is_accepted(self):
        plugin, _ = await self.prepared()
        event = self.audience(True, "")
        event.message_obj.raw_message = {}
        from enum import Enum
        class MessageType(Enum):
            FRIEND_MESSAGE = "FriendMessage"
            GROUP_MESSAGE = "GroupMessage"
        event.get_message_type = lambda: MessageType.FRIEND_MESSAGE
        self.assertTrue(plugin._can_show_audit_details(event))
        event.get_message_type = lambda: MessageType.GROUP_MESSAGE
        self.assertFalse(plugin._can_show_audit_details(event))

    async def test_get_profile_text_without_event_is_public(self):
        plugin, _ = await self.prepared()
        text = await plugin.get_profile_text("11111")
        self.assertIn("不适宜内容", text)
        self.assert_public_text(text)
        model = await plugin._build_profile_model("11111", None)
        self.assertFalse(model["audit_details"])
        self.assert_public_projection(model)

    async def test_eventless_text_api_uses_group_material_after_private_fusion(self):
        from test_scenes import SceneTests
        plugin = await SceneTests.prepared(self)
        plugin.config["cross_scene_link_enabled"] = True
        plugin._fetch_stranger_info = AsyncMock(return_value={})
        plugin._prepare_avatar_bytes = AsyncMock(return_value=None)
        await plugin.get_profile_tags_with_score("11111", self.audience(False, ""))
        text = await plugin.get_profile_text("11111")
        self.assertIn("group_IMPRESSION", text)
        for marker in ("PRIVATE_RAW", "private_IMPRESSION", "private_TRAIT", "private_EVIDENCE", "private_MEMORY", "FUSED_PRIVATE_DERIVED", "FUSED_TRAIT", "FUSED_MEMORY"):
            self.assertNotIn(marker, text)
        self.assert_public_text(text)

    async def test_trusted_decision_api_retains_diagnostics(self):
        plugin, original = await self.prepared()
        result = await plugin.get_decision_profile("11111", self.audience())
        self.assertEqual(result["review_level"], "中等")
        self.assertFalse(result["review_pending"])
        self.assertEqual(result["risk_components"], {"semantic": 36})
        self.assertEqual(result["tags"], original["tags"])
        self.assertTrue(result["evidence_untrusted"])

    async def test_daily_context_never_includes_review_even_for_admin_private(self):
        plugin, result = await self.prepared()
        for admin, group in ((False, "22222"), (True, "22222"), (False, ""), (True, "")):
            with self.subTest(admin=admin, group=group):
                with patch("main.cached_view", return_value=result):
                    text = await plugin._build_conversation_profile_context("11111", self.audience(admin, group))
                self.assert_public_text(text)
                self.assertIn("不适宜内容", text)

    async def test_tool_does_not_fetch_stranger_or_avatar(self):
        plugin, _ = await self.prepared()
        text = await plugin.user_profile_query(self.audience(), "11111")
        self.assert_public_text(text)
        plugin._fetch_stranger_info.assert_not_awaited()
        plugin._prepare_avatar_bytes.assert_not_awaited()

    async def test_tool_missing_identity_is_denied_before_loading_profile(self):
        plugin, _ = await self.prepared()
        for event in (None, Event({}, sender="")):
            text = await plugin.user_profile_query(event, "11111")
            self.assertIn("拒绝", text)
        plugin.get_profile_tags_with_score.assert_not_awaited()

    async def test_tool_blacklisted_caller_does_not_load_profile(self):
        plugin, _ = await self.prepared()
        plugin._sender_silenced_by_ban = AsyncMock(return_value=True)
        text = await plugin.user_profile_query(self.audience(), "11111")
        self.assertNotIn("不适宜内容", text)
        plugin._sender_silenced_by_ban.assert_awaited_once_with("11111")
        plugin.get_profile_tags_with_score.assert_not_awaited()

    async def test_non_boolean_admin_never_authorizes_other_user_or_private_fusion(self):
        from scene_profiles import scene_for
        plugin, _ = await self.prepared(cross_scene_link_enabled=True)
        for value in ("True", "true", 1):
            with self.subTest(value=value):
                event = self.audience(value, "")
                event._sender = "33333"
                self.assertIs(plugin._event_identity(event)[2], False)
                self.assertFalse(plugin._check_query_permission("11111", event)[0])
                self.assertEqual(scene_for(plugin, "11111", event), "group")
                self.assertIn("拒绝", await plugin.user_profile_query(event, "11111"))
        plugin.get_profile_tags_with_score.assert_not_awaited()
        real_admin = self.audience(True, "")
        real_admin._sender = "33333"
        self.assertTrue(plugin._check_query_permission("11111", real_admin)[0])
        self.assertEqual(scene_for(plugin, "11111", real_admin), "fused")
        from test_scenes import SceneTests
        actual = await SceneTests.prepared(self)
        actual.config["cross_scene_link_enabled"] = True
        await actual.get_profile_tags_with_score("11111", self.audience(False, ""))
        for value in ("True", 1):
            event = self.audience(value, "")
            event._sender = "33333"
            result = await actual.get_profile_tags_with_score("11111", event)
            self.assertEqual(result["scene"], "group")
            self.assertNotIn("FUSED_PRIVATE_DERIVED", str(result))
            self.assertNotIn("private_IMPRESSION", str(result))

    async def test_blacklisted_tool_caller_is_stopped_before_all_early_returns(self):
        from unittest.mock import Mock
        cases = (({}, "33333"), ({}, "invalid-qq"),
                 ({"enable_llm_tool": False}, "11111"), ({"enable": False}, "11111"))
        for config, target in cases:
            with self.subTest(config=config, target=target):
                plugin, _ = await self.prepared(**config)
                plugin._sender_silenced_by_ban = AsyncMock(return_value=True)
                event = self.audience()
                event.stop_event = Mock()
                text = await plugin.user_profile_query(event, target)
                self.assertEqual(text, "")
                event.stop_event.assert_called_once_with()
                plugin._sender_silenced_by_ban.assert_awaited_once_with("11111")
                plugin.get_profile_tags_with_score.assert_not_awaited()
                self.assertEqual(event.sent, [])

    async def management_fixture(self):
        import time
        plugin = self.make_plugin({"history_scan_enabled": False, "friend_reconcile_enabled": False,
                                   "llm_tags": False, "management_resolution_decay_days": 30})
        await plugin._ensure_loaded()
        plugin._fetch_stranger_info = AsyncMock(return_value={})
        plugin._load_invite_guard_records = AsyncMock(return_value=[])
        plugin._load_ban_entry = AsyncMock(return_value=["在 bot 黑名单中（原因：BAN_REASON_SENTINEL）"])
        now = int(time.time())
        for key, event_type, at, resolution in (("ban", "astrbot_banned", now - 20 * 86400, ""),
                                                ("unban", "astrbot_unbanned", now - 15 * 86400, "normal")):
            status = await plugin.record_management_event(
                "11111", event_key=key, event_type=event_type, source="guard", event_time=at,
                reason="MANAGEMENT_REASON_SENTINEL", resolution=resolution)
            self.assertEqual(status["status"], "recorded")
        status = await plugin.record_bot_mute_event(
            "11111", event_key="mute", group_id="33333", event_time=now - 20 * 86400,
            duration=60, operator_id="11111", risk_increment=35, risk_cap=70, self_id="99999")
        self.assertEqual(status["status"], "recorded")
        return plugin

    def assert_management_summary(self, value):
        for forbidden in ("MANAGEMENT_REASON_SENTINEL", "BAN_REASON_SENTINEL", "历史风险剩余", "历史风险已结清", "结清类型"):
            self.assertNotIn(forbidden, str(value))

    async def test_management_records_public_models_text_and_daily_context_are_summaries(self):
        plugin = await self.management_fixture()
        for admin, group in ((False, "22222"), (True, "22222"), (False, ""), (True, "")):
            with self.subTest(admin=admin, group=group):
                event = self.audience(admin, group)
                model = await plugin._build_profile_model("11111", event)
                text = plugin._profile_model_to_text(model)
                self.assertIn("黑名单", text)
                self.assertIn("禁言 bot", text)
                if admin and not group:
                    self.assertIn("MANAGEMENT_REASON_SENTINEL", text)
                    self.assertIn("历史风险剩余 50%", text)
                else:
                    self.assert_management_summary(model)
                    self.assert_management_summary(text)
                context = await plugin._build_conversation_profile_context("11111", event)
                self.assertIn("黑名单", context)
                self.assert_management_summary(context)
        trusted = await plugin.get_decision_profile("11111", self.audience())
        self.assertIn("MANAGEMENT_REASON_SENTINEL", str(trusted["management"]["recent"]))
        self.assertEqual(trusted["management"]["state"]["risk_factors"]["astrbot_banned"]["factor"], 0.5)

    async def test_management_and_mute_formatters_default_to_summary(self):
        plugin = await self.management_fixture()
        management = plugin._format_management_events("11111")
        self.assertIn("黑名单", str(management))
        self.assert_management_summary(management)
        details = plugin._format_management_events("11111", include_details=True)
        self.assertIn("MANAGEMENT_REASON_SENTINEL", str(details))
        self.assertIn("历史风险剩余 50%", str(details))
        mute = plugin._format_personal_mute_events("11111")
        self.assertIn("亲自禁言 bot", str(mute))
        self.assertNotIn("操作者", str(mute))
        self.assertNotIn("时长", str(mute))
        mute_details = plugin._format_personal_mute_events("11111", include_details=True)
        self.assertIn("操作者 11111", str(mute_details))
        self.assertIn("时长", str(mute_details))

    async def test_tool_exception_does_not_expose_internal_message(self):
        plugin, _ = await self.prepared()
        plugin.get_profile_tags_with_score.side_effect = RuntimeError("INTERNAL_EVIDENCE review_level=高敏感")
        text = await plugin.user_profile_query(self.audience(), "11111")
        self.assert_public_text(text)
        self.assertIn("失败", text)

    async def test_chat_exception_does_not_expose_internal_message(self):
        plugin, _ = await self.prepared()
        plugin.get_profile_tags_with_score.side_effect = RuntimeError("INTERNAL_EVIDENCE review_level=高敏感")
        event = self.audience()
        await plugin._send_profile("11111", event)
        self.assertTrue(event.sent)
        self.assert_public_text(str(event.sent))
        self.assertIn("失败", str(event.sent))

    async def test_image_fallbacks_use_public_text(self):
        plugin, _ = await self.prepared(image_output=True)
        for failure in (None, RuntimeError("INTERNAL_EVIDENCE")):
            with self.subTest(failure=failure):
                event = self.audience(True, "22222")
                with patch("main.HAS_PIL", True), patch.object(plugin, "_render_profile_image", return_value=None, side_effect=failure) as render:
                    await plugin._send_profile("11111", event)
                self.assertTrue(event.sent)
                self.assert_public_text(str(event.sent))
                self.assertFalse(render.call_args.args[0]["audit_details"])

    @staticmethod
    def ranking_model():
        return {
            "scope": "全部", "group_id": "", "page": 1, "pages": 1,
            "total": 1, "out_of_range": False,
            "items": [{
                "qq": "11111", "score": 36, "level": "中",
                "evidence": ["INTERNAL_EVIDENCE"], "tag_sources": ["INTERNAL_SOURCE"],
                "semantic_status": "INTERNAL_STATUS", "semantic_time": 1234567890,
                "risk_components": {"semantic": 36},
                "management_count": 2, "currently_banned": False,
            }],
        }

    async def test_ranking_text_defaults_to_public_without_audit_flag(self):
        plugin, _ = await self.prepared()
        model = self.ranking_model()
        text = plugin._ranking_model_to_text(model)
        self.assertIn("QQ 11111", text)
        self.assertIn("36 分（中）", text)
        self.assert_public_text(text)
        for value in ("INTERNAL_STATUS", "主要依据：", "标签来源：", "历史管理事件：", "当前黑名单："):
            self.assertNotIn(value, text)
        model["audit_details"] = True
        details = plugin._ranking_model_to_text(model)
        for value in ("INTERNAL_EVIDENCE", "INTERNAL_SOURCE", "INTERNAL_STATUS"):
            self.assertIn(value, details)

    async def test_ranking_command_crops_group_model_and_keeps_private_admin_details(self):
        plugin, _ = await self.prepared(image_output=True)
        original = self.ranking_model()
        plugin._build_ranking_model = AsyncMock(return_value=original)
        for group in ("22222", ""):
            with self.subTest(group=group):
                event = self.audience(True, group)
                event._text = "/画像排名 全部"
                with patch("main.HAS_PIL", True), patch.object(plugin, "_render_ranking_image", return_value=None) as render:
                    await plugin.profile_ranking_command(event)
                model = render.call_args.args[0]
                self.assertEqual(model["audit_details"], not group)
                self.assertIn("36 分（中）", str(event.sent))
                for key in ("evidence", "tag_sources", "semantic_status", "semantic_time", "risk_components"):
                    if group:
                        self.assertNotIn(key, model["items"][0])
                    else:
                        self.assertEqual(model["items"][0][key], original["items"][0][key])
                if group:
                    self.assert_public_text(str(event.sent))
                else:
                    self.assertIn("INTERNAL_EVIDENCE", str(event.sent))
                    self.assertIn("INTERNAL_SOURCE", str(event.sent))
                self.assertEqual(original, self.ranking_model())

    async def test_ranking_generation_exception_is_generic(self):
        plugin, _ = await self.prepared()
        plugin._build_ranking_model = AsyncMock(side_effect=RuntimeError("INTERNAL_EVIDENCE review_level=高敏感"))
        for group in ("22222", ""):
            event = self.audience(True, group)
            event._text = "/画像排名 全部"
            await plugin.profile_ranking_command(event)
            self.assertIn("失败", str(event.sent))
            self.assert_public_text(str(event.sent))

    async def test_ranking_image_exception_falls_back_to_public_text(self):
        plugin, _ = await self.prepared(image_output=True)
        plugin._build_ranking_model = AsyncMock(return_value=self.ranking_model())
        event = self.audience(True, "22222")
        event._text = "/画像排名 全部"
        with patch("main.HAS_PIL", True), patch.object(plugin, "_render_ranking_image", side_effect=RuntimeError("INTERNAL_EVIDENCE")):
            await plugin.profile_ranking_command(event)
        self.assertEqual(event.sent[0][0][0], "plain")
        self.assertIn("36 分（中）", str(event.sent))
        self.assert_public_text(str(event.sent))

    async def test_legacy_self_helper_blacklist_is_silent_before_profile_loading(self):
        plugin, _ = await self.prepared()
        plugin._sender_silenced_by_ban = AsyncMock(return_value=True)
        for admin, group in ((False, "22222"), (True, "22222"), (False, ""), (True, "")):
            event = self.audience(admin, group)
            await plugin._send_self_profile(event)
            self.assertEqual(event.sent, [])
        self.assertEqual(plugin._sender_silenced_by_ban.await_count, 4)
        for call in plugin._sender_silenced_by_ban.await_args_list:
            self.assertEqual(call.args, ("11111",))
        plugin.get_profile_tags_with_score.assert_not_awaited()
        plugin._fetch_stranger_info.assert_not_awaited()

    async def test_real_image_drawing_never_receives_internal_diagnostics(self):
        from PIL import Image, ImageDraw
        plugin, _ = await self.prepared()
        original_text = ImageDraw.ImageDraw.text
        for admin, group in ((False, "22222"), (True, "22222"), (False, ""), (True, "")):
            with self.subTest(admin=admin, group=group):
                model = await plugin._build_profile_model("11111", self.audience(admin, group))
                drawn = []
                def capture(draw, xy, text, *args, **kwargs):
                    drawn.append(str(text))
                    return original_text(draw, xy, text, *args, **kwargs)
                with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
                    with patch("main.__file__", os.path.join(directory, "main.py")), patch.object(ImageDraw.ImageDraw, "text", capture):
                        path = plugin._render_profile_image(model)
                    self.assertIsNotNone(path)
                    with Image.open(path) as image:
                        image.load()
                        self.assertEqual(image.width, 960)
                text = "\n".join(drawn)
                self.assertIn("不适宜内容", text)
                if admin and not group:
                    self.assertIn("审查", text)
                    self.assertIn("INTERNAL_EVIDENCE", text)
                else:
                    self.assert_public_text(text)
