import asyncio
import inspect
import json
import re
import time
from datetime import datetime
from typing import Any

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, register

try:
    from astrbot.core import sp
except Exception:  # 内部模块缺失时前科联动自动关闭
    sp = None

try:
    from astrbot.core.agent.run_context import ContextWrapper
except Exception:  # 兼容旧版 / 内部 API 缺失
    ContextWrapper = None


# 加群邀请守卫的 plugin_id（author/name 全小写，见 StarMetadata.plugin_id）
INVITE_GUARD_STAR_NAME = "astrbot_plugin_group_invite_guard"
INVITE_GUARD_PLUGIN_ID = "kimi/astrbot_plugin_group_invite_guard"
QQ_TOOLS_STAR_NAME = "astrbot_plugin_qq_tools"

_QUOTE_MAX_LEN = 200  # 单条原话最长保留字符数
_FLUSH_INTERVAL_SEC = 60  # 内存统计落盘间隔
_SUMMARY_MAX_CHARS = 100  # LLM 印象小结字数上限
_HISTORY_QUOTE_KEEP = 10  # 会话历史补充原话的最大条数

_STATS_KEY = "up_stats"
_QUOTES_KEY = "up_quotes"
_SUMMARY_KEY = "up_summaries"


def _unwrap_event(event):
    """@filter.llm_tool 在 v4.26+ 传入 ContextWrapper，这里取出内部 AstrMessageEvent。"""
    if ContextWrapper is not None and isinstance(event, ContextWrapper):
        try:
            return event.context.event
        except Exception:
            return event
    return event


def _fmt_time(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


def _new_stat() -> dict:
    return {
        "g_count": 0,
        "g_first": 0,
        "g_last": 0,
        "groups": {},
        "p_count": 0,
        "p_first": 0,
        "p_last": 0,
    }


@register(
    "astrbot_plugin_user_profile",
    "Kimi",
    "公开可查的 QQ 用户画像：被动采集发言统计与原话，LLM 浓缩印象小结，联动加群邀请守卫的前科记录",
    "1.0.0",
)
class UserProfilePlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config or {}
        self._stats: dict | None = None  # qq -> 统计（懒加载，定期落盘）
        self._quotes: dict | None = None  # qq -> 最近原话列表
        self._dirty = False
        self._flush_task: asyncio.Task | None = None
        self._store_lock = asyncio.Lock()
        self._summary_lock = asyncio.Lock()  # 防止并发查询重复调 LLM

    # ---------------- 被动采集（零 LLM 零网络） ----------------

    @filter.event_message_type(
        filter.EventMessageType.GROUP_MESSAGE | filter.EventMessageType.PRIVATE_MESSAGE
    )
    async def on_message(self, event: AstrMessageEvent):
        """静默统计每个 QQ 的发言；不打断事件，不影响正常回复流程。"""
        if not self.config.get("passive_collect", True):
            return
        qq = str(event.get_sender_id() or "").strip()
        if not qq:
            return
        # 不记录机器人自己的发言
        self_id = str(getattr(getattr(event, "message_obj", None), "self_id", "") or "")
        if self_id and self_id == qq:
            return

        text = (event.get_message_str() or "").strip()
        group_id = str(event.get_group_id() or "").strip()
        now = int(time.time())

        await self._ensure_loaded()
        async with self._store_lock:
            st = self._stats.setdefault(qq, _new_stat())
            if group_id:
                st["g_count"] = int(st.get("g_count") or 0) + 1
                st["g_last"] = now
                if not st.get("g_first"):
                    st["g_first"] = now
                groups = st.setdefault("groups", {})
                groups[group_id] = int(groups.get(group_id, 0) or 0) + 1
            else:
                st["p_count"] = int(st.get("p_count") or 0) + 1
                st["p_last"] = now
                if not st.get("p_first"):
                    st["p_first"] = now

            if text and not text.startswith("/"):
                if len(text) > _QUOTE_MAX_LEN:
                    text = text[:_QUOTE_MAX_LEN] + "…"
                qlist = self._quotes.setdefault(qq, [])
                qlist.append(
                    {
                        "t": now,
                        "src": f"群 {group_id}" if group_id else "私聊",
                        "text": text,
                    }
                )
                del qlist[: -self._quote_keep()]
            # 控制规模：跟踪人数超上限时清掉最不活跃的，防内存/体积无限涨
            cap = self._max_tracked()
            if len(self._stats) > cap:
                self._prune_oldest(cap)
            self._dirty = True

        self._ensure_flush_task()

    # ---------------- 查询入口 ----------------

    @filter.command("画像")
    async def profile_command(self, event: AstrMessageEvent):
        text = (event.get_message_str() or "").strip()
        match = re.search(r"\d{5,12}", text)
        if not match:
            await event.send(MessageChain(chain=[Plain("用法：/画像 <QQ号>")]))
            return
        qq = match.group(0)

        if not self.config.get("public_query", True):
            try:
                if not event.is_admin():
                    await event.send(
                        MessageChain(chain=[Plain("当前仅管理员可查询用户画像")])
                    )
                    return
            except Exception:
                pass  # is_admin 不可用时按公开处理

        profile = await self._build_profile(qq, event)
        try:
            await event.send(
                MessageChain(
                    chain=[
                        Image.fromURL(f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=100"),
                        Plain(profile),
                    ]
                )
            )
        except Exception:
            # 头像发不出来（非 QQ 平台/网络问题）时回退纯文本
            try:
                await event.send(MessageChain(chain=[Plain(profile)]))
            except Exception as exc:
                logger.error(f"user_profile: send profile failed: {exc}")

    @filter.llm_tool(name="user_profile_query")
    async def user_profile_query(self, event, qq: str):
        """查询指定 QQ 号用户的画像：发言活跃度、发言风格印象、加群邀请守卫前科与黑名单记录。需要了解某个 QQ 用户的背景时调用。"""
        event = _unwrap_event(event)
        qq = str(qq or "").strip()
        if not re.fullmatch(r"\d{5,12}", qq):
            return "查询失败：请提供 5-12 位纯数字 QQ 号。"
        return await self._build_profile(qq, event)

    # ---------------- 画像组装 ----------------

    async def _build_profile(self, qq: str, event) -> str:
        await self._ensure_loaded()
        st = self._stats.get(qq) or {}
        quotes = list(self._quotes.get(qq) or [])

        # 昵称 / 邀请守卫前科 / 黑名单互不依赖，并发拉取，单点失败互不影响
        fetched = await asyncio.gather(
            self._fetch_stranger_info(qq, event),
            self._load_invite_guard_records(qq),
            self._load_ban_entry(qq),
            return_exceptions=True,
        )
        stranger = fetched[0] if isinstance(fetched[0], dict) else None
        guard_lines = fetched[1] if isinstance(fetched[1], list) else []
        ban_lines = fetched[2] if isinstance(fetched[2], list) else []

        # 自己没采集到时，用 AstrBot 会话历史补充原话
        history_lines = []
        if not quotes:
            history_lines = await self._search_history_quotes(qq)

        summary = ""
        if quotes:
            summary = await self._get_or_make_summary(qq, st, quotes)

        nickname = str((stranger or {}).get("nickname") or "").strip()
        header = f"【用户画像】QQ {qq}" + (f"（{nickname}）" if nickname else "")
        sections = [header]

        activity = self._format_activity(st)
        if activity:
            sections.append(activity)

        style = self._format_style(quotes, history_lines, summary)
        if style:
            sections.append(style)

        record_lines = guard_lines + ban_lines
        if record_lines:
            sections.append("前科记录：\n" + "\n".join(f"- {line}" for line in record_lines))

        if len(sections) == 1:
            return f"{header}\n暂无记录：未采集到该用户的发言，也没有前科数据。"
        return "\n\n".join(sections)

    # ---------------- 对外 API（供加群邀请守卫等插件读取画像） ----------------

    async def get_profile_text(self, qq: str, event=None) -> str:
        """返回指定 QQ 的画像文本，只读、无副作用，供其它插件调用。

        调用方式（以加群邀请守卫为例）::

            md = self.context.get_registered_star("astrbot_plugin_user_profile")
            instance = getattr(md, "star_cls", None)
            if instance is not None and hasattr(instance, "get_profile_text"):
                text = await instance.get_profile_text(inviter_qq, event)

        event 可传 None：缺少 OneBot 上下文时自动跳过昵称/头像段。
        未采集到该用户任何数据时返回空字符串（方便调用方拼 prompt 时判空）。
        """
        qq = str(qq or "").strip()
        if not re.fullmatch(r"\d{5,12}", qq):
            return ""
        profile = await self._build_profile(qq, event)
        return "" if "暂无记录" in profile else profile

    def _format_activity(self, st: dict) -> str:
        if not st:
            return ""
        g = int(st.get("g_count") or 0)
        p = int(st.get("p_count") or 0)
        if not g and not p:
            return ""
        groups = st.get("groups") or {}
        lines = ["活跃度：", f"- 发言总数：{g + p}（群聊 {g} / 私聊 {p}）"]
        if groups:
            lines.append(f"- 活跃群数：{len(groups)}")
        last = max(int(st.get("g_last") or 0), int(st.get("p_last") or 0))
        if last:
            lines.append(f"- 最近发言：{_fmt_time(last)}")
        firsts = [x for x in (st.get("g_first"), st.get("p_first")) if x]
        if firsts:
            lines.append(f"- 首次记录：{_fmt_time(min(firsts))}")
        return "\n".join(lines)

    def _format_style(self, quotes: list, history_lines: list, summary: str) -> str:
        parts = []
        if summary:
            parts.append(f"印象小结：{summary}")
        if quotes:
            shown = quotes[-5:]
            lines = [
                f"[{_fmt_time(q.get('t'))}] ({q.get('src')}) {q.get('text')}"
                for q in shown
            ]
            parts.append("最近发言摘录：\n" + "\n".join(lines))
        elif history_lines:
            parts.append(
                "历史会话中的发言（来自 AstrBot 会话记录补充）：\n"
                + "\n".join(history_lines)
            )
        return "\n\n".join(parts)

    # ---------------- LLM 印象小结（按发言数缓存） ----------------

    async def _get_or_make_summary(self, qq: str, st: dict, quotes: list) -> str:
        if not self.config.get("llm_summary", True):
            return ""
        total = int(st.get("g_count") or 0) + int(st.get("p_count") or 0)
        try:
            summaries = await self.get_kv_data(_SUMMARY_KEY, {})
        except Exception as exc:
            logger.warning(f"user_profile: load summaries failed: {exc}")
            summaries = {}
        if not isinstance(summaries, dict):
            summaries = {}
        cached = summaries.get(qq)

        def _cached_text() -> str:
            return str(cached.get("text") or "") if isinstance(cached, dict) else ""

        # 发言数没变，直接用缓存，不重复调 LLM
        if isinstance(cached, dict) and int(cached.get("count") or -1) == total:
            return _cached_text()

        async with self._summary_lock:
            # 并发查询时另一个请求可能已生成，进锁后复查
            cached = summaries.get(qq)
            if isinstance(cached, dict) and int(cached.get("count") or -1) == total:
                return str(cached.get("text") or "")

            provider_id = self.config.get("llm_provider_id") or self._default_provider_id()
            if not provider_id:
                return _cached_text()

            material = "\n".join(
                f"- {q.get('text')}" for q in quotes[-20:] if q.get("text")
            )
            prompt = (
                f"以下是 QQ 用户 {qq} 的最近发言摘录：\n{material}\n\n"
                f"请用不超过 {_SUMMARY_MAX_CHARS} 字概括这个人的发言风格和给人的印象，"
                "只输出小结本身，不要解释，不要任何前缀。"
            )
            try:
                resp = await self.context.llm_generate(
                    chat_provider_id=provider_id, prompt=prompt
                )
                text = (getattr(resp, "completion_text", "") or "").strip()
            except Exception as exc:
                logger.warning(f"user_profile: LLM summary failed: {exc}")
                text = ""
            if not text:
                # LLM 失败时降级：有旧缓存用旧缓存，没有就只列原话
                return _cached_text()

            summaries[qq] = {"count": total, "text": text[:500], "time": int(time.time())}
            try:
                await self.put_kv_data(_SUMMARY_KEY, summaries)
            except Exception as exc:
                logger.warning(f"user_profile: save summary failed: {exc}")
            return text

    # ---------------- 前科联动（均优雅降级） ----------------

    async def _load_invite_guard_records(self, qq: str) -> list:
        """读取加群邀请守卫的邀请/拉群/禁言记录中与此 QQ 相关的条目；未装守卫或读取失败返回空。"""
        if sp is None:
            return []
        try:
            if self.context.get_registered_star(INVITE_GUARD_STAR_NAME) is None:
                return []
        except Exception:
            return []

        async def _get(key):
            try:
                return await sp.get_async("plugin", INVITE_GUARD_PLUGIN_ID, key, {})
            except Exception as exc:
                logger.warning(f"user_profile: read invite-guard '{key}' failed: {exc}")
                return {}

        invite, join, mute = await asyncio.gather(
            _get("invite_records"), _get("join_records"), _get("mute_records")
        )

        lines = []
        if isinstance(invite, dict):
            for gid, rec in invite.items():
                inviter = (
                    str(rec.get("inviter") or "").strip()
                    if isinstance(rec, dict)
                    else str(rec or "").strip()
                )
                if inviter != qq:
                    continue
                ts = _fmt_time(rec.get("time")) if isinstance(rec, dict) else "-"
                action = (
                    str(rec.get("action") or "").strip() or "-"
                    if isinstance(rec, dict)
                    else "-"
                )
                extra = ""
                if isinstance(mute, dict) and gid in mute:
                    extra = f"；该群累计禁言 bot {mute[gid]} 次"
                lines.append(f"曾邀请 bot 进群 {gid}（{ts}，{action}{extra}）")
        if isinstance(join, dict):
            for gid, rec in join.items():
                if not isinstance(rec, dict):
                    continue
                if str(rec.get("operator") or "").strip() != qq:
                    continue
                lines.append(f"曾操作拉 bot 进群 {gid}（{_fmt_time(rec.get('time'))}）")
        return lines[:10]

    async def _load_ban_entry(self, qq: str) -> list:
        """读取 qq_tools 黑名单中此 QQ 的条目；未装 qq_tools 或读取失败返回空。"""
        try:
            md = self.context.get_registered_star(QQ_TOOLS_STAR_NAME)
        except Exception as exc:
            logger.warning(f"user_profile: get qq_tools instance failed: {exc}")
            return []
        instance = getattr(md, "star_cls", None) if md else None
        config = getattr(instance, "config", None) if instance else None
        if config is None:
            return []
        try:
            ban_list = config.get("ban_list")
        except Exception as exc:
            logger.warning(f"user_profile: read ban_list failed: {exc}")
            return []
        if not isinstance(ban_list, list):
            return []
        lines = []
        for item in ban_list:
            if not isinstance(item, dict):
                continue
            if str(item.get("user_id") or "").strip() != qq:
                continue
            reason = str(item.get("reason") or "未注明")
            lines.append(f"在 bot 黑名单中（{_fmt_time(item.get('ban_time'))}，原因：{reason}）")
        return lines

    # ---------------- 会话历史补充 ----------------

    async def _search_history_quotes(self, qq: str) -> list:
        """从 AstrBot 会话历史里抓该 QQ 的发言原话（自己未采集到时的补充来源）。"""
        cm = getattr(self.context, "conversation_manager", None)
        if cm is None:
            return []
        try:
            conversations, _ = await cm.get_filtered_conversations(
                page=1, page_size=10, search_query=qq, include_history=True
            )
        except Exception as exc:
            logger.warning(f"user_profile: history search '{qq}' failed: {exc}")
            return []

        pattern = re.compile(r"ID:\s*" + re.escape(qq))
        quotes = []
        for conv in conversations or []:
            history = getattr(conv, "history", None)
            if not history:
                continue
            try:
                items = json.loads(history)
            except Exception:
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("role") or "").strip().lower() != "user":
                    continue
                text = self._content_to_text(item.get("content"))
                for raw_line in text.splitlines():
                    if not pattern.search(raw_line):
                        continue
                    line = re.sub(r"^\s*\[[^\]]*\]\s*", "", raw_line)
                    line = re.sub(r"^\s*\S+\s*\(ID:[^)]*\)\s*[:：]\s*", "", line)
                    line = re.sub(r"^\s*\[At:[^\]]*\]\s*", "", line).strip()
                    if len(line) < 2:
                        continue
                    if len(line) > _QUOTE_MAX_LEN:
                        line = line[:_QUOTE_MAX_LEN] + "…"
                    if line not in quotes:
                        quotes.append(line)
            if len(quotes) >= _HISTORY_QUOTE_KEEP:
                break
        return quotes[:_HISTORY_QUOTE_KEEP]

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return " ".join(parts).strip()
        return str(content).strip()

    # ---------------- 存储 ----------------

    async def _ensure_loaded(self):
        if self._stats is not None:
            return
        try:
            stats = await self.get_kv_data(_STATS_KEY, {})
        except Exception as exc:
            logger.warning(f"user_profile: load stats failed: {exc}")
            stats = {}
        try:
            quotes = await self.get_kv_data(_QUOTES_KEY, {})
        except Exception as exc:
            logger.warning(f"user_profile: load quotes failed: {exc}")
            quotes = {}
        self._stats = stats if isinstance(stats, dict) else {}
        self._quotes = quotes if isinstance(quotes, dict) else {}

    def _ensure_flush_task(self):
        try:
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._flush_loop())
        except RuntimeError:
            pass  # 没有运行中的事件循环时由 terminate 兜底落盘

    async def _flush_loop(self):
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL_SEC)
            try:
                await self._flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"user_profile: flush failed: {exc}")

    async def _flush(self):
        if not self._dirty or self._stats is None:
            return
        async with self._store_lock:
            if not self._dirty:
                return
            await self.put_kv_data(_STATS_KEY, self._stats)
            await self.put_kv_data(_QUOTES_KEY, self._quotes or {})
            self._dirty = False

    async def terminate(self):
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        try:
            await self._flush()
        except Exception as exc:
            logger.warning(f"user_profile: final flush failed: {exc}")

    # ---------------- 工具 ----------------

    def _quote_keep(self) -> int:
        try:
            return max(1, int(self.config.get("quote_keep", 10) or 10))
        except (TypeError, ValueError):
            return 10

    def _max_tracked(self) -> int:
        try:
            return max(100, int(self.config.get("max_tracked_users", 5000) or 5000))
        except (TypeError, ValueError):
            return 5000

    def _prune_oldest(self, cap: int):
        """按最近活跃时间清掉最不活跃的用户直到规模回到 cap。调用方需持有 _store_lock。"""

        def _last(item):
            s = item[1]
            return max(int(s.get("g_last") or 0), int(s.get("p_last") or 0))

        victims = sorted(self._stats.items(), key=_last)[: len(self._stats) - cap]
        for old_qq, _ in victims:
            self._stats.pop(old_qq, None)
            if self._quotes is not None:
                self._quotes.pop(old_qq, None)

    async def _fetch_stranger_info(self, qq: str, event) -> dict | None:
        """取昵称等信息（OneBot get_stranger_info）；非 OneBot 平台或失败返回 None。"""
        bot = getattr(event, "bot", None)
        if bot is None:
            return None
        try:
            info = await self._call_action(
                bot, "get_stranger_info", user_id=int(qq), no_cache=False
            )
        except Exception as exc:
            logger.warning(f"user_profile: get_stranger_info {qq} failed: {exc}")
            return None
        return info if isinstance(info, dict) else None

    async def _call_action(self, bot: Any, action: str, **params: Any) -> Any:
        method = getattr(bot, action, None)
        if callable(method):
            result = method(**params)
            return await result if inspect.isawaitable(result) else result
        for name in ("call_action", "call_api"):
            fn = getattr(bot, name, None)
            if callable(fn):
                result = fn(action, **params)
                return await result if inspect.isawaitable(result) else result
        raise RuntimeError(f"no usable OneBot action caller for {action}")

    def _default_provider_id(self) -> str:
        provider_settings = self._nested(self._global_config(), "provider_settings")
        if isinstance(provider_settings, dict):
            return str(provider_settings.get("default_provider_id") or "")
        return str(self._nested(provider_settings, "default_provider_id") or "")

    def _global_config(self) -> Any:
        try:
            return self.context.get_config()
        except Exception:
            return None

    @staticmethod
    def _nested(obj: Any, *keys: str) -> Any:
        for key in keys:
            if obj is None:
                return None
            if isinstance(obj, dict):
                obj = obj.get(key)
                continue
            getter = getattr(obj, "get", None)
            if callable(getter):
                try:
                    obj = getter(key, None)
                    continue
                except Exception:
                    pass
            obj = getattr(obj, key, None)
        return obj
