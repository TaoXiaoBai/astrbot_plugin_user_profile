import asyncio
import copy
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch


def decorator(*args, **kwargs):
    def apply(obj):
        return obj
    return apply


class Filter:
    class CustomFilter:
        pass
    class EventMessageType:
        GROUP_MESSAGE = 1
        PRIVATE_MESSAGE = 2
        OTHER_MESSAGE = 4
        ALL = 7
    custom_filter = staticmethod(decorator)
    event_message_type = staticmethod(decorator)
    command = staticmethod(decorator)
    regex = staticmethod(decorator)
    llm_tool = staticmethod(decorator)


class Star:
    def __init__(self, context=None):
        self.context = context


class Image:
    @classmethod
    def fromFileSystem(cls, path):
        return ("image", path)


astrbot = types.ModuleType("astrbot")
astrbot.__path__ = []
api = types.ModuleType("astrbot.api")
event = types.ModuleType("astrbot.api.event")
components = types.ModuleType("astrbot.api.message_components")
star = types.ModuleType("astrbot.api.star")
core = types.ModuleType("astrbot.core")
api.logger = types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None)
event.filter = Filter
event.AstrMessageEvent = object
event.MessageChain = lambda chain: chain
components.Image = Image
components.Plain = lambda text: ("plain", text)
components.At = type("At", (), {})
star.Context = object
star.Star = Star
star.register = decorator
core.sp = None
sys.modules.update({
    "astrbot": astrbot, "astrbot.api": api, "astrbot.api.event": event,
    "astrbot.api.message_components": components, "astrbot.api.star": star,
    "astrbot.core": core,
})

from main import (
    MessageEventFilter,
    SocialEventFilter,
    TagEngine,
    UserProfilePlugin,
    _new_stat,
    _risk_level,
)


class Event:
    def __init__(self, raw, sender="11111", group="22222", text="", messages=None):
        self.message_obj = types.SimpleNamespace(raw_message=raw, self_id="99999")
        self._sender, self._group, self._text = sender, group, text
        self._messages = messages or []
        self.sent = []
    def get_sender_id(self): return self._sender
    def get_group_id(self): return self._group
    def get_message_str(self): return self._text
    def get_messages(self): return self._messages
    def is_admin(self): return False
    async def send(self, chain): self.sent.append(chain)


class PluginTests(unittest.IsolatedAsyncioTestCase):
    def make_plugin(self, config=None):
        plugin = UserProfilePlugin(types.SimpleNamespace(), config or {})
        plugin._kv = {}
        async def get(key, default): return copy.deepcopy(plugin._kv.get(key, default))
        async def put(key, value): plugin._kv[key] = copy.deepcopy(value)
        plugin.get_kv_data = get
        plugin.put_kv_data = put
        return plugin

    def test_handlers_strictly_split_message_and_social_events(self):
        msg = Event({"post_type": "message", "message_type": "group"})
        notice = Event({"post_type": "notice", "notice_type": "friend_add"})
        self.assertTrue(MessageEventFilter().filter(msg, None))
        self.assertFalse(MessageEventFilter().filter(notice, None))
        self.assertTrue(SocialEventFilter().filter(notice, None))
        self.assertFalse(SocialEventFilter().filter(msg, None))

    def test_zero_risk_threshold_is_respected(self):
        self.assertEqual(_risk_level(0, {
            "risk_level_low": 0,
            "risk_level_high": 0,
            "risk_level_extreme": 0,
        }), "极高")

    def test_invalid_custom_risk_weights_never_break_scoring(self):
        plugin = self.make_plugin({
            "risk_weights": {
                "friendly": "nan",
                "normal": float("inf"),
                "scam_suspect": "not-a-number",
                "unknown_tag": 999999,
                "helpful": -20,
            }
        })
        weights = plugin._risk_weights()
        self.assertEqual(weights["friendly"], -10)
        self.assertEqual(weights["normal"], -15)
        self.assertEqual(weights["scam_suspect"], 20)
        self.assertNotIn("unknown_tag", weights)
        self.assertEqual(weights["helpful"], -20)
        score = plugin._calc_risk_score([
            {"tag": "helpful", "confidence": 1},
            {"tag": "friendly", "confidence": "nan"},
            {"tag": "normal", "confidence": "broken"},
            {"tag": "scam_suspect", "confidence": float("inf")},
        ])
        self.assertEqual(score, 30)

    def test_invalid_json_risk_weights_fall_back_safely(self):
        plugin = self.make_plugin({
            "risk_weights": '{"friendly": NaN, "helpful": "bad"}'
        })
        self.assertEqual(plugin._risk_weights()["friendly"], -10)
        self.assertEqual(plugin._calc_risk_score([
            {"tag": "friendly", "confidence": 1}
        ]), 40)

    def test_query_permission_defaults_to_self_only(self):
        plugin = self.make_plugin()
        own = Event({}, sender="11111")
        other = Event({}, sender="22222")
        self.assertTrue(plugin._check_query_permission("11111", own)[0])
        self.assertFalse(plugin._check_query_permission("11111", other)[0])
        other.is_admin = lambda: True
        self.assertTrue(plugin._check_query_permission("11111", other)[0])

    async def test_llm_timeout_configuration_is_applied(self):
        config = self.make_plugin({
            "llm_provider_id": "provider",
            "llm_timeout_seconds": 7,
        }).config
        engine = TagEngine(config)
        context = types.SimpleNamespace(llm_generate=AsyncMock())

        async def timeout(awaitable, *, timeout):
            awaitable.close()
            self.assertEqual(timeout, 7)
            raise asyncio.TimeoutError

        with patch("main.asyncio.wait_for", side_effect=timeout):
            with self.assertRaises(asyncio.TimeoutError):
                await engine.generate_llm_tags(
                    "11111", _new_stat(), [{"text": "hello"}], [], context, config
                )

    async def test_social_event_prefers_raw_user_id(self):
        plugin = self.make_plugin()
        raw = {"post_type": "notice", "notice_type": "friend_add", "user_id": 33333, "time": 10}
        await plugin.on_social_event(Event(raw, sender="99999"))
        self.assertIn("33333", plugin._stats)
        self.assertNotIn("99999", plugin._stats)

    async def test_empty_non_image_message_is_not_counted_as_image(self):
        plugin = self.make_plugin()
        raw = {"post_type": "message", "message_type": "group", "user_id": 11111}
        await plugin.on_message(Event(raw, text="", messages=[types.SimpleNamespace()]))
        self.assertEqual(plugin._stats["11111"]["images"], 0)

    async def test_private_quote_storage_can_be_disabled(self):
        plugin = self.make_plugin({"store_private_quotes": False})
        raw = {"post_type": "message", "message_type": "private", "user_id": 11111}
        await plugin.on_message(Event(raw, group="", text="private secret"))
        self.assertEqual(plugin._stats["11111"]["p_count"], 1)
        self.assertEqual(plugin._quotes.get("11111", []), [])

    async def test_llm_singleflight_and_empty_result_cache(self):
        plugin = self.make_plugin()
        calls = 0
        async def generate(*args, **kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return []
        plugin._tag_engine.generate_llm_tags = generate
        st = _new_stat(); st["g_count"] = 1
        quotes = [{"t": 1, "src": "群 1", "text": "hello"}]
        first, second = await asyncio.gather(
            plugin._get_or_make_llm_tags("11111", st, quotes, []),
            plugin._get_or_make_llm_tags("11111", st, quotes, []),
        )
        self.assertEqual((first, second), ([], []))
        self.assertEqual(calls, 1)
        await plugin._get_or_make_llm_tags("11111", st, quotes, [])
        self.assertEqual(calls, 1)

    async def test_llm_cache_misses_when_signals_or_base_tags_change(self):
        plugin = self.make_plugin()
        plugin._tag_engine.generate_llm_tags = AsyncMock(return_value=[])
        quotes = [{"t": 1, "src": "群 1", "text": "same quote"}]
        st = _new_stat()
        st["g_count"] = 1
        tags = [{"tag": "active_low", "confidence": 0.8, "source": "stats"}]
        await plugin._get_or_make_llm_tags("11111", st, quotes, tags)
        await plugin._get_or_make_llm_tags("11111", st, quotes, tags)
        self.assertEqual(plugin._tag_engine.generate_llm_tags.await_count, 1)
        changed_stats = dict(st)
        changed_stats["images"] = 1
        await plugin._get_or_make_llm_tags("11111", changed_stats, quotes, tags)
        changed_tags = [{"tag": "image_spammer", "confidence": 0.9, "source": "stats"}]
        await plugin._get_or_make_llm_tags("11111", changed_stats, quotes, changed_tags)
        self.assertEqual(plugin._tag_engine.generate_llm_tags.await_count, 3)

    async def test_delete_blocks_inflight_llm_writeback_but_allows_new_messages(self):
        plugin = self.make_plugin()
        await plugin._ensure_loaded()
        plugin._stats["11111"] = _new_stat()
        plugin._quotes["11111"] = [{"t": 1, "src": "群 1", "text": "old"}]
        entered = asyncio.Event()
        release = asyncio.Event()

        async def generate(*args, **kwargs):
            entered.set()
            await release.wait()
            return [{"tag": "friendly", "confidence": 1, "source": "llm"}]

        plugin._tag_engine.generate_llm_tags = generate
        flight = asyncio.create_task(plugin._get_or_make_llm_tags(
            "11111", plugin._stats["11111"], plugin._quotes["11111"], []
        ))
        await entered.wait()
        await plugin.delete_profile_command(Event(
            {}, sender="11111", group="", text="/画像删除 自己"
        ))
        release.set()
        await flight
        self.assertNotIn("11111", plugin._stats)
        self.assertNotIn("11111", plugin._quotes)
        self.assertNotIn("11111", plugin._tags_cache)
        self.assertNotIn("11111", plugin._kv.get("up_stats", {}))
        self.assertNotIn("11111", plugin._kv.get("up_quotes", {}))
        self.assertNotIn("11111", plugin._kv.get("up_tags", {}))

        raw = {"post_type": "message", "message_type": "group", "user_id": 11111}
        await plugin.on_message(Event(raw, text="new message"))
        self.assertEqual(plugin._stats["11111"]["g_count"], 1)
        await plugin.terminate()

    async def test_delete_blocks_inflight_history_writeback(self):
        plugin = self.make_plugin({"history_scan_cooldown": 0})
        await plugin._ensure_loaded()
        plugin._stats["11111"] = _new_stat()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def scan(*args, **kwargs):
            entered.set()
            await release.wait()
            return {
                "first": 1, "last": 2, "complete": True,
                "quotes": ["old history"], "next_page": 1,
                "scanned_count": 1, "failed": False,
            }

        plugin._scan_history = scan
        flight = asyncio.create_task(plugin._ensure_history_scanned("11111", force=True))
        await entered.wait()
        await plugin.delete_profile_command(Event(
            {}, sender="11111", group="", text="/画像删除 自己"
        ))
        release.set()
        self.assertFalse(await flight)
        self.assertNotIn("11111", plugin._stats)
        self.assertNotIn("11111", plugin._quotes)
        self.assertNotIn("11111", plugin._tags_cache)
        self.assertNotIn("11111", plugin._kv.get("up_stats", {}))
        self.assertNotIn("11111", plugin._kv.get("up_quotes", {}))
        self.assertNotIn("11111", plugin._kv.get("up_tags", {}))

    async def test_older_fingerprint_flight_cannot_overwrite_newer_cache(self):
        plugin = self.make_plugin()
        old_entered = asyncio.Event()
        release_old = asyncio.Event()

        async def generate(qq, st, quotes, *args):
            if quotes[0]["text"] == "old":
                old_entered.set()
                await release_old.wait()
                raise RuntimeError("old failed")
            return [{"tag": "friendly", "confidence": 1, "source": "llm"}]

        plugin._tag_engine.generate_llm_tags = generate
        st = _new_stat()
        old_task = asyncio.create_task(plugin._get_or_make_llm_tags(
            "11111", st, [{"text": "old"}], []
        ))
        await old_entered.wait()
        await plugin._get_or_make_llm_tags("11111", st, [{"text": "new"}], [])
        release_old.set()
        await old_task
        self.assertEqual(plugin._tags_cache["11111"]["status"], "success")
        self.assertEqual(plugin._tags_cache["11111"]["tags"][0]["tag"], "friendly")
        self.assertEqual(plugin._llm_status["11111"], "success")

    async def test_history_scan_resumes_next_page_and_force_restarts(self):
        plugin = self.make_plugin({
            "history_scan_pages": 1,
            "history_scan_page_size": 1,
            "history_scan_cooldown": 0,
        })
        manager = types.SimpleNamespace(get_filtered_conversations=AsyncMock(side_effect=[
            ([types.SimpleNamespace(created_at=1, updated_at=2, history='[]')], 2),
            ([types.SimpleNamespace(created_at=3, updated_at=4, history='[]')], 2),
            ([types.SimpleNamespace(created_at=1, updated_at=2, history='[]')], 1),
        ]))
        plugin.context = types.SimpleNamespace(conversation_manager=manager)
        await plugin._ensure_history_scanned("11111")
        self.assertEqual(plugin._stats["11111"]["history_next_page"], 2)
        await plugin._ensure_history_scanned("11111")
        self.assertTrue(plugin._stats["11111"]["history_complete"])
        await plugin._ensure_history_scanned("11111", force=True)
        self.assertEqual(manager.get_filtered_conversations.await_args.kwargs["page"], 1)

    async def test_history_page_failure_preserves_progress_and_retries_failed_page(self):
        plugin = self.make_plugin({
            "history_scan_pages": 3,
            "history_scan_page_size": 1,
            "history_scan_cooldown": 0,
        })
        first = types.SimpleNamespace(
            created_at=10,
            updated_at=20,
            history='[{"role":"user","content":"A (ID: 11111): retained quote"}]',
        )
        second = types.SimpleNamespace(
            created_at=30,
            updated_at=40,
            history='[{"role":"user","content":"A (ID: 11111): resumed quote"}]',
        )
        manager = types.SimpleNamespace(get_filtered_conversations=AsyncMock(
            side_effect=[([first], 2), RuntimeError("page failed"), ([second], 2)]
        ))
        plugin.context = types.SimpleNamespace(conversation_manager=manager)
        await plugin._ensure_history_scanned("11111")
        stat = plugin._stats["11111"]
        self.assertFalse(stat["history_complete"])
        self.assertEqual(stat["history_next_page"], 2)
        self.assertEqual(stat["history_first"], 10)
        self.assertEqual(stat["history_last"], 20)
        self.assertEqual(stat["history_quotes"], ["retained quote"])
        self.assertEqual(stat["history_last_error"], "history_scan_failed")

        await plugin._ensure_history_scanned("11111")
        stat = plugin._stats["11111"]
        self.assertTrue(stat["history_complete"])
        self.assertEqual(stat["history_quotes"], ["retained quote", "resumed quote"])
        self.assertEqual(stat["history_last_error"], "")
        self.assertEqual(
            manager.get_filtered_conversations.await_args.kwargs["page"], 2
        )

    async def test_batch_history_scan_reports_progress_and_precise_totals(self):
        plugin = self.make_plugin({"history_scan_batch_limit": 5})
        await plugin._ensure_loaded()
        for qq in ("11111", "11112", "11113", "11114", "11115", "11116"):
            plugin._stats[qq] = _new_stat()

        async def scan(qq, force=False, restart=None):
            await asyncio.sleep(0)
            if qq == "11114":
                raise RuntimeError("scan failed")
            plugin._stats[qq]["history_complete"] = qq != "11115"
            if qq == "11111":
                plugin._stats[qq]["history_first"] = 1
            return True

        plugin._ensure_history_scanned = AsyncMock(side_effect=scan)
        plugin._flush = AsyncMock()
        event = Event({}, sender="99999", group="22222", text="/画像扫描 全部")
        event.is_admin = lambda: True
        await plugin.history_scan_command(event)
        messages = [part[1] for chain in event.sent for part in chain]
        progress = [text for text in messages if text.startswith("历史扫描进度：")]
        self.assertGreaterEqual(len(progress), 1)
        self.assertTrue(any("已处理 2/5" in text for text in progress))
        final = messages[-1]
        self.assertIn("本次调用成功 4 人，失败 1 人", final)
        self.assertIn("本批分页完成 3 人，仍未完成分页 1 人", final)
        self.assertIn("批次外剩余 1 人", final)
        self.assertIn("回填历史时间 1 人", final)
        self.assertEqual(plugin._ensure_history_scanned.await_count, 5)
        for call in plugin._ensure_history_scanned.await_args_list:
            self.assertTrue(call.kwargs["force"])
            self.assertFalse(call.kwargs["restart"])

    def test_real_image_render_initializes_draw_and_uses_exact_height(self):
        from PIL import Image as PILImage
        plugin = self.make_plugin({"image_output": True})
        path = plugin._render_profile_image("11111", "标题\n" + "很长的内容" * 200)
        self.assertIsNotNone(path)
        try:
            with PILImage.open(path) as image:
                self.assertEqual(image.width, 900)
                self.assertGreater(image.height, 500)
            self.assertRegex(os.path.basename(path), r"^profile_11111_[0-9a-f]{32}\.png$")
        finally:
            if path and os.path.exists(path):
                os.remove(path)

    async def test_rendered_temp_file_is_removed_after_send_failure(self):
        plugin = self.make_plugin({"image_output": True})
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        event = Event({"post_type": "message"})
        event.send = AsyncMock(side_effect=[RuntimeError("send failed"), None])
        plugin._build_profile_text = AsyncMock(return_value="profile")
        with patch.object(plugin, "_render_profile_image", return_value=path):
            await plugin._send_profile("11111", event)
        self.assertFalse(os.path.exists(path))

    async def test_profile_prefers_versioned_invite_guard_api(self):
        guard = types.SimpleNamespace(get_inviter_evidence=AsyncMock(return_value={
            "schema_version": 1,
            "invite": {"30000": [{"inviter": "11111", "request_key": "old"}]},
            "join": {},
            "group_mute_context": {"30000": 3},
        }))
        plugin = self.make_plugin()
        plugin.context = types.SimpleNamespace(
            get_registered_star=lambda name: types.SimpleNamespace(star_cls=guard)
        )
        records = await plugin._load_guard_records_raw("11111", "current")
        self.assertIn("30000", records["invite"])
        self.assertEqual(records["mute"], {})
        guard.get_inviter_evidence.assert_awaited_once_with(
            "11111", exclude_request_key="current"
        )

    async def test_public_group_hides_private_quotes_and_friend_request_comment(self):
        plugin = self.make_plugin({"show_quotes": True})
        await plugin._ensure_loaded()
        plugin._stats["11111"] = _new_stat()
        plugin._stats["11111"].update({
            "g_count": 1,
            "friend_add_time": 1,
            "friend_request_comment": "private verification",
        })
        plugin._quotes["11111"] = [
            {"t": 1, "src": "私聊", "text": "secret"},
            {"t": 2, "src": "群 22222", "text": "public"},
        ]
        plugin.get_profile_tags_with_score = AsyncMock(return_value={
            "score": 50, "level": "中",
            "tags": [{"tag": "normal", "confidence": 1, "source": "stats"}],
        })
        group_text = await plugin._build_profile_text(
            "11111", Event({}, group="22222")
        )
        self.assertNotIn("secret", group_text)
        self.assertNotIn("private verification", group_text)
        self.assertIn("public", group_text)
        private_text = await plugin._build_profile_text(
            "11111", Event({}, sender="11111", group="")
        )
        self.assertIn("private verification", private_text)
        self.assertIn("secret", private_text)

    async def test_loaded_retention_marks_dirty_and_persists_on_terminate(self):
        now = 2_000_000
        plugin = self.make_plugin({"quote_retention_days": 1})
        plugin._kv["up_quotes"] = {
            "11111": [
                {"t": now - 90_000, "text": "expired"},
                {"t": now, "text": "kept"},
            ],
            "22222": {"t": now, "text": "dirty container"},
        }
        with patch("main.time.time", return_value=now):
            await plugin._ensure_loaded()
            self.assertTrue(plugin._dirty)
            await plugin.terminate()
        self.assertEqual(
            plugin._kv["up_quotes"]["11111"], [{"t": now, "text": "kept"}]
        )
        self.assertNotIn("22222", plugin._kv["up_quotes"])

    async def test_message_retention_only_scans_current_user(self):
        now = 2_000_000
        plugin = self.make_plugin({"quote_retention_days": 1})
        await plugin._ensure_loaded()
        plugin._quotes["22222"] = [{"t": 1, "text": "other old quote"}]
        raw = {"post_type": "message", "message_type": "group", "user_id": 11111}
        with patch("main.time.time", return_value=now):
            await plugin.on_message(Event(raw, text="current"))
        self.assertIn("22222", plugin._quotes)
        await plugin.terminate()

    async def test_history_manager_failure_reaches_decision_partial_errors(self):
        manager = types.SimpleNamespace(
            get_filtered_conversations=AsyncMock(side_effect=RuntimeError("db down"))
        )
        context = types.SimpleNamespace(
            conversation_manager=manager,
            get_registered_star=lambda name: None,
        )
        plugin = UserProfilePlugin(context, {"llm_tags": False})
        plugin._kv = {}

        async def get(key, default):
            return copy.deepcopy(plugin._kv.get(key, default))

        async def put(key, value):
            plugin._kv[key] = copy.deepcopy(value)

        plugin.get_kv_data = get
        plugin.put_kv_data = put
        await plugin._ensure_loaded()
        plugin._stats["11111"] = _new_stat()
        plugin._stats["11111"]["g_count"] = 1
        profile = await plugin.get_decision_profile("11111")
        self.assertIn("history_scan_failed", profile["partial_errors"])
        self.assertEqual(
            plugin._stats["11111"]["history_last_error"], "history_scan_failed"
        )

    async def test_llm_failure_reaches_decision_partial_errors(self):
        context = types.SimpleNamespace(
            get_registered_star=lambda name: None,
            llm_generate=AsyncMock(side_effect=RuntimeError("provider down")),
        )
        plugin = UserProfilePlugin(context, {
            "history_scan_enabled": False,
            "llm_provider_id": "provider",
        })
        plugin._kv = {}

        async def get(key, default):
            return copy.deepcopy(plugin._kv.get(key, default))

        async def put(key, value):
            plugin._kv[key] = copy.deepcopy(value)

        plugin.get_kv_data = get
        plugin.put_kv_data = put
        await plugin._ensure_loaded()
        plugin._stats["11111"] = _new_stat()
        plugin._stats["11111"]["g_count"] = 1
        plugin._quotes["11111"] = [{"t": 1, "src": "群 1", "text": "hello"}]
        profile = await plugin.get_decision_profile("11111")
        self.assertEqual(profile["llm_status"], "error")
        self.assertIn("llm_analysis_failed", profile["partial_errors"])
        context.llm_generate.assert_awaited_once()

    async def test_decision_profile_reports_known_partial_errors(self):
        plugin = self.make_plugin()
        await plugin._ensure_loaded()
        plugin._stats["11111"] = _new_stat()
        plugin._stats["11111"].update({
            "g_count": 1,
            "history_last_error": "history_scan_failed",
        })
        plugin._llm_status["11111"] = "cached_error"
        plugin.get_profile_tags_with_score = AsyncMock(return_value={
            "score": 50, "level": "中",
            "tags": [{"tag": "active_low", "confidence": 0.8, "source": "stats"}],
        })
        profile = await plugin.get_decision_profile("11111")
        self.assertEqual(profile["llm_status"], "cached_error")
        self.assertEqual(
            profile["partial_errors"],
            ["history_scan_failed", "llm_analysis_failed"],
        )


if __name__ == "__main__":
    unittest.main()
