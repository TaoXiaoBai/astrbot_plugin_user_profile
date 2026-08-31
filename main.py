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
_HISTORY_QUOTE_KEEP = 10  # 会话历史补充原话的最大条数

_STATS_KEY = "up_stats"
_QUOTES_KEY = "up_quotes"
_TAGS_KEY = "up_tags"


class TagEngine:
    """标签生成引擎：把原始统计、原话、前科转换成结构化标签。"""

    def __init__(self, config: dict):
        self.config = config or {}

    def generate_base_tags(self, qq: str, st: dict, quotes: list, guard_records: dict, ban_lines: list) -> list[dict]:
        """零 LLM、零网络，从统计和规则里秒出基础标签。"""
        st = st or {}
        tags = []
        now = int(time.time())
        g_count = int(st.get("g_count") or 0)
        p_count = int(st.get("p_count") or 0)
        total = g_count + p_count
        groups = st.get("groups") or {}
        group_count = len(groups)
        g_first = int(st.get("g_first") or 0)
        p_first = int(st.get("p_first") or 0)
        first_seen = min([x for x in (g_first, p_first) if x]) or 0
        last_seen = max([x for x in (st.get("g_last") or 0, st.get("p_last") or 0) if x]) or 0

        # 活跃度标签
        high = int(self.config.get("tag_active_high_threshold", 100) or 100)
        med = int(self.config.get("tag_active_med_threshold", 20) or 20)
        if total >= high:
            tags.append(self._tag("active_high", 0.95, "stats", f"发言总数 {total}（群聊 {g_count} / 私聊 {p_count}）"))
        elif total >= med:
            tags.append(self._tag("active_medium", 0.85, "stats", f"发言总数 {total}"))
        elif total > 0:
            tags.append(self._tag("active_low", 0.8, "stats", f"发言总数仅 {total}"))

        # 新人标签
        newcomer_days = int(self.config.get("tag_newcomer_days", 7) or 7)
        if first_seen and (now - first_seen) <= newcomer_days * 86400:
            tags.append(self._tag("newcomer", 0.85, "stats", f"首次记录于 {_fmt_time(first_seen)}"))

        # 多群出现
        multi_threshold = int(self.config.get("tag_multi_group_threshold", 3) or 3)
        if group_count >= multi_threshold:
            tags.append(self._tag("multi_group", 0.9, "stats", f"活跃群数 {group_count}"))

        # 私聊活跃
        if p_count > 0:
            tags.append(self._tag("private_active", 0.85, "stats", f"私聊发言 {p_count} 条"))

        # 沉寂/回归
        if last_seen and (now - last_seen) > 30 * 86400:
            tags.append(self._tag("long_inactive", 0.75, "stats", f"最近发言 {_fmt_time(last_seen)}"))

        # 风险前科标签
        invite_records = guard_records.get("invite") or {}
        join_records = guard_records.get("join") or {}
        mute_records = guard_records.get("mute") or {}

        invite_times = 0
        invite_rejected = 0
        for gid, rec in invite_records.items():
            inviter = (rec.get("inviter") or "") if isinstance(rec, dict) else str(rec)
            if str(inviter).strip() != qq:
                continue
            invite_times += 1
            action = str(rec.get("action") or "").strip() if isinstance(rec, dict) else ""
            if action in ("reject", "拒绝", "blacklist", "拉黑"):
                invite_rejected += 1

        kick_times = 0
        for gid, rec in join_records.items():
            if not isinstance(rec, dict):
                continue
            if str(rec.get("operator") or "").strip() == qq:
                kick_times += 1

        mute_times = 0
        if isinstance(mute_records, dict):
            for k, v in mute_records.items():
                if str(k) == qq:
                    mute_times += 1
                elif isinstance(v, dict) and str(v.get("operator") or "").strip() == qq:
                    mute_times += 1

        if invite_times >= 2:
            tags.append(self._tag("frequent_inviter", 0.85, "guard", f"累计邀请 bot 进群 {invite_times} 次"))
        elif invite_times == 1:
            tags.append(self._tag("inviter", 0.75, "guard", "曾邀请 bot 进群 1 次"))

        if invite_rejected:
            tags.append(self._tag("invite_rejected", 0.9, "guard", f"邀请被处理 {invite_rejected} 次"))

        if kick_times:
            tags.append(self._tag("kick_history", 0.9, "guard", f"操作拉 bot 进群 {kick_times} 次"))

        if mute_times:
            tags.append(self._tag("mute_history", 0.9, "guard", f"相关禁言记录 {mute_times} 条"))

        if ban_lines:
            tags.append(self._tag("ban_history", 0.95, "ban_list", f"在 bot 黑名单中（{len(ban_lines)} 条记录）"))

        return tags

    async def generate_llm_tags(self, qq: str, st: dict, quotes: list, base_tags: list, context: Context, config: dict) -> list[dict]:
        """调用 LLM 从原话里打语义标签。默认开启，token 暂不考虑。"""
        if not config.get("llm_tags", True):
            return []
        if not quotes:
            return []

        provider_id = config.get("llm_provider_id") or self._default_provider_id(context)
        if not provider_id:
            return []

        material = "\n".join(
            f"- {q.get('text')}" for q in quotes[-20:] if q.get("text")
        )
        base_desc = ", ".join(t["tag"] for t in base_tags[:8]) or "无"

        prompt = (
            f"你正在为一个 QQ 用户打标签，用于辅助判断此人进群是否有风险。\n"
            f"QQ: {qq}\n"
            f"基础统计标签：{base_desc}\n"
            f"最近发言摘录（按时间从早到晚）：\n{material}\n\n"
            "请从以下维度中挑选 0-5 个最显著的标签返回 JSON 数组，不要返回任何解释：\n"
            "- spam_suspect（刷屏/垃圾信息嫌疑）\n"
            "- ad_suspect（广告/引流嫌疑）\n"
            "- troll（抬杠/钓鱼/挑衅）\n"
            "- friendly（语气友好）\n"
            "- helpful（乐于助人）\n"
            "- nsfw_tendency（不适宜内容倾向）\n"
            "- political_sensitive（政治敏感倾向）\n"
            "- scam_suspect（诈骗/索要信息嫌疑）\n"
            "- repetitive（重复发相同内容）\n"
            "- normal（看起来正常，无明显风险）\n\n"
            "返回格式示例：\n"
            '[{"tag": "ad_suspect", "confidence": 0.82, "reason": "多次发送二维码和联系方式"}]\n'
            "confidence 必须是 0.0-1.0 之间的数字，reason 用一句话说明理由。只输出 JSON。"
        )

        try:
            resp = await context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
            text = (getattr(resp, "completion_text", "") or "").strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "tags" in parsed:
                parsed = parsed["tags"]
            if not isinstance(parsed, list):
                return []
            out = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                tag = str(item.get("tag") or "").strip().lower()
                conf = float(item.get("confidence") or 0)
                reason = str(item.get("reason") or "").strip()
                if tag and 0 <= conf <= 1:
                    out.append(self._tag(tag, conf, "llm", reason))
            return out[:5]
        except Exception as exc:
            logger.warning(f"user_profile: LLM tags failed: {exc}")
            return []

    @staticmethod
    def _tag(tag: str, confidence: float, source: str, evidence: str = "") -> dict:
        return {
            "tag": tag,
            "confidence": round(confidence, 2),
            "source": source,
            "evidence": evidence,
        }

    @staticmethod
    def _default_provider_id(context: Context) -> str:
        try:
            gc = context.get_config()
        except Exception:
            return ""
        if isinstance(gc, dict):
            ps = gc.get("provider_settings") or {}
            if isinstance(ps, dict):
                return str(ps.get("default_provider_id") or "")
        ps = getattr(gc, "provider_settings", None)
        if ps is not None:
            return str(getattr(ps, "default_provider_id", "") or "")
        return ""


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
    "QQ 用户画像 / 自动标签引擎：被动采集群聊与私聊发言，自动打上活跃度、风险、社交、内容等结构化标签，供加群邀请守卫等插件在决策时调用",
    "1.1.0",
)
class UserProfilePlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config or {}
        self._stats: dict | None = None  # qq -> 统计（懒加载，定期落盘）
        self._quotes: dict | None = None  # qq -> 最近原话列表
        self._tags: dict | None = None  # qq -> 标签缓存
        self._dirty = False
        self._flush_task: asyncio.Task | None = None
        self._store_lock = asyncio.Lock()
        self._tag_lock = asyncio.Lock()  # 防止并发查询重复调 LLM 打标签
        self._tag_engine = TagEngine(self.config)

    def _enabled(self) -> bool:
        return bool(self.config.get("enable", True))

    # ---------------- 被动采集（零 LLM 零网络） ----------------

    @filter.event_message_type(
        filter.EventMessageType.GROUP_MESSAGE | filter.EventMessageType.PRIVATE_MESSAGE
    )
    async def on_message(self, event: AstrMessageEvent):
        """静默统计每个 QQ 的发言；不打断事件，不影响正常回复流程。"""
        if not self._enabled():
            return
        if not self.config.get("passive_collect", True):
            return
        qq = str(event.get_sender_id() or "").strip()
        if not qq:
            return
        # 不记录机器人自己的发言
        self_id = str(getattr(getattr(event, "message_obj", None), "self_id", "") or "")
        if self_id and self_id == qq:
            return

        group_id = str(event.get_group_id() or "").strip()
        if not group_id and not self.config.get("collect_private", True):
            return
        if group_id and not self._group_allowed(group_id):
            return

        text = (event.get_message_str() or "").strip()
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
        if not self._enabled():
            return
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

        profile = await self._build_profile_text(qq, event)
        chain = [Plain(profile)]
        if self.config.get("show_avatar", True):
            chain.insert(0, Image.fromURL(f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=100"))
        try:
            await event.send(MessageChain(chain=chain))
        except Exception:
            # 头像发不出来（非 QQ 平台/网络问题）时回退纯文本
            try:
                await event.send(MessageChain(chain=[Plain(profile)]))
            except Exception as exc:
                logger.error(f"user_profile: send profile failed: {exc}")

    @filter.llm_tool(name="user_profile_query")
    async def user_profile_query(self, event, qq: str):
        """查询指定 QQ 号用户的画像标签：活跃度、风险标签、LLM 语义标签。用于判断该用户是否可信、是否适合进群。需要了解某个 QQ 用户的背景时调用。"""
        event = _unwrap_event(event)
        if not self._enabled():
            return "用户画像插件当前未启用。"
        qq = str(qq or "").strip()
        if not re.fullmatch(r"\d{5,12}", qq):
            return "查询失败：请提供 5-12 位纯数字 QQ 号。"
        tags = await self.get_profile_tags(qq, event)
        if not tags:
            return f"QQ {qq} 暂无画像记录。"
        lines = [f"QQ {qq} 的标签："]
        for t in tags:
            lines.append(f"- {t['tag']}（置信度 {t['confidence']}，来源 {t['source']}）{t.get('evidence', '')}")
        return "\n".join(lines)

    # ---------------- 画像组装 ----------------

    async def _build_profile_text(self, qq: str, event) -> str:
        """给 /画像 命令用：返回人类可读文本。"""
        tags = await self.get_profile_tags(qq, event)
        if not tags:
            return f"【用户画像】QQ {qq}\n暂无记录：未采集到该用户的发言，也没有前科数据。"

        await self._ensure_loaded()
        st = self._quotes.get(qq) or {}

        base = [t for t in tags if t["source"] != "llm"]
        llm = [t for t in tags if t["source"] == "llm"]

        lines = [f"【用户画像】QQ {qq}"]

        # 昵称
        stranger = await self._fetch_stranger_info(qq, event)
        nickname = str((stranger or {}).get("nickname") or "").strip()
        if nickname:
            lines[0] += f"（{nickname}）"

        # 基础标签
        if base:
            tag_line = " | ".join(
                f"{t['tag']}({int(t['confidence'] * 100)}%)" for t in base
            )
            lines.append(f"基础标签：{tag_line}")

        # LLM 标签
        if llm:
            tag_line = " | ".join(
                f"{t['tag']}({int(t['confidence'] * 100)}%)" for t in llm
            )
            lines.append(f"LLM 标签：{tag_line}")

        # 活跃度
        stats = self._stats.get(qq) or {}
        activity = self._format_activity(stats)
        if activity:
            lines.append(activity)

        # 最近原话
        quotes = list(self._quotes.get(qq) or [])
        if not quotes:
            history_lines = await self._search_history_quotes(qq)
            if history_lines:
                lines.append("历史会话中的发言（来自 AstrBot 会话记录补充）：\n" + "\n".join(history_lines))
        else:
            shown = quotes[-self._quote_show() :]
            q_lines = [
                f"[{_fmt_time(q.get('t'))}] ({q.get('src')}) {q.get('text')}"
                for q in shown
            ]
            lines.append("最近发言摘录：\n" + "\n".join(q_lines))

        return "\n\n".join(lines)

    # ---------------- 对外 API（供加群邀请守卫等插件读取标签） ----------------

    async def get_profile_tags(self, qq: str, event=None) -> list[dict]:
        """返回指定 QQ 的结构化标签列表，供加群邀请守卫等插件在决策时调用。

        调用方式（以加群邀请守卫为例）::

            md = self.context.get_registered_star("astrbot_plugin_user_profile")
            instance = getattr(md, "star_cls", None)
            if instance is not None and hasattr(instance, "get_profile_tags"):
                tags = await instance.get_profile_tags(inviter_qq, event)

        event 可传 None：缺少 OneBot 上下文时自动跳过昵称/头像。
        未采集到该用户任何数据时返回 []（方便调用方直接判空）。
        """
        if not self._enabled():
            return []
        qq = str(qq or "").strip()
        if not re.fullmatch(r"\d{5,12}", qq):
            return []

        await self._ensure_loaded()
        st = self._stats.get(qq) or {}
        quotes = list(self._quotes.get(qq) or [])

        # 自己没采集到时，用 AstrBot 会话历史补充原话
        history_lines = []
        if not quotes and self.config.get("history_fallback", True):
            history_lines = await self._search_history_quotes(qq)
            quotes = [{"t": 0, "src": "历史会话", "text": line} for line in history_lines]

        total = int(st.get("g_count") or 0) + int(st.get("p_count") or 0)
        if not total and not quotes:
            return []

        # 前科与黑名单并发拉取
        fetched = await asyncio.gather(
            self._load_guard_records_raw(qq),
            self._load_ban_entry(qq),
            return_exceptions=True,
        )
        guard_records = fetched[0] if isinstance(fetched[0], dict) else {"invite": {}, "join": {}, "mute": {}}
        ban_lines = fetched[1] if isinstance(fetched[1], list) else []

        # 基础标签
        base_tags = self._tag_engine.generate_base_tags(qq, st, quotes, guard_records, ban_lines)

        # LLM 标签（默认开启，带缓存）
        llm_tags = await self._get_or_make_llm_tags(qq, st, quotes, base_tags)

        # 合并：基础标签在前，LLM 标签在后，按置信度降序
        all_tags = base_tags + llm_tags
        all_tags.sort(key=lambda x: x["confidence"], reverse=True)
        return all_tags

    async def get_profile_text(self, qq: str, event=None) -> str:
        """返回指定 QQ 的画像文本；未采集到数据时返回空字符串，兼容旧版调用。"""
        if not self._enabled():
            return ""
        qq = str(qq or "").strip()
        if not re.fullmatch(r"\d{5,12}", qq):
            return ""
        profile = await self._build_profile_text(qq, event)
        return "" if "暂无记录" in profile else profile

    # ---------------- LLM 标签（按发言数缓存） ----------------

    async def _get_or_make_llm_tags(self, qq: str, st: dict, quotes: list, base_tags: list) -> list[dict]:
        total = int(st.get("g_count") or 0) + int(st.get("p_count") or 0)
        try:
            tags_cache = await self.get_kv_data(_TAGS_KEY, {})
        except Exception as exc:
            logger.warning(f"user_profile: load tags cache failed: {exc}")
            tags_cache = {}
        if not isinstance(tags_cache, dict):
            tags_cache = {}
        cached = tags_cache.get(qq)

        def _cached_tags() -> list:
            if isinstance(cached, dict) and cached.get("count") == total:
                return list(cached.get("tags") or [])
            return []

        cached_tags = _cached_tags()
        if cached_tags:
            return cached_tags

        async with self._tag_lock:
            cached = tags_cache.get(qq)
            cached_tags = _cached_tags()
            if cached_tags:
                return cached_tags

            llm_tags = await self._tag_engine.generate_llm_tags(
                qq, st, quotes, base_tags, self.context, self.config
            )
            if llm_tags:
                tags_cache[qq] = {"count": total, "tags": llm_tags, "time": int(time.time())}
                try:
                    await self.put_kv_data(_TAGS_KEY, tags_cache)
                except Exception as exc:
                    logger.warning(f"user_profile: save tags cache failed: {exc}")
            return llm_tags

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

    # ---------------- 前科联动（均优雅降级） ----------------

    async def _load_guard_records_raw(self, qq: str) -> dict:
        """读取加群邀请守卫的原始记录，返回 {"invite": {}, "join": {}, "mute": {}}；未装守卫或失败返回空结构。"""
        empty = {"invite": {}, "join": {}, "mute": {}}
        if sp is None or not self.config.get("link_invite_guard", True):
            return empty
        try:
            if self.context.get_registered_star(INVITE_GUARD_STAR_NAME) is None:
                return empty
        except Exception:
            return empty

        async def _get(key):
            try:
                return await sp.get_async("plugin", INVITE_GUARD_PLUGIN_ID, key, {})
            except Exception as exc:
                logger.warning(f"user_profile: read invite-guard '{key}' failed: {exc}")
                return {}

        invite, join, mute = await asyncio.gather(
            _get("invite_records"), _get("join_records"), _get("mute_records")
        )

        # 过滤出与此 QQ 相关的记录
        out = {"invite": {}, "join": {}, "mute": {}}
        if isinstance(invite, dict):
            for gid, rec in invite.items():
                inviter = (
                    str(rec.get("inviter") or "").strip()
                    if isinstance(rec, dict)
                    else str(rec or "").strip()
                )
                if inviter == qq:
                    out["invite"][gid] = rec if isinstance(rec, dict) else {"inviter": qq}
        if isinstance(join, dict):
            for gid, rec in join.items():
                if isinstance(rec, dict) and str(rec.get("operator") or "").strip() == qq:
                    out["join"][gid] = rec
        if isinstance(mute, dict):
            out["mute"] = mute
        return out

    async def _load_invite_guard_records(self, qq: str) -> list:
        """读取加群邀请守卫的邀请/拉群/禁言记录中与此 QQ 相关的条目；未装守卫或读取失败返回空。"""
        records = await self._load_guard_records_raw(qq)
        invite = records.get("invite") or {}
        join = records.get("join") or {}
        mute = records.get("mute") or {}

        lines = []
        for gid, rec in invite.items():
            ts = _fmt_time(rec.get("time")) if isinstance(rec, dict) else "-"
            action = str(rec.get("action") or "").strip() or "-" if isinstance(rec, dict) else "-"
            extra = ""
            if isinstance(mute, dict) and gid in mute:
                extra = f"；该群累计禁言 bot {mute[gid]} 次"
            lines.append(f"曾邀请 bot 进群 {gid}（{ts}，{action}{extra}）")
        for gid, rec in join.items():
            if isinstance(rec, dict):
                lines.append(f"曾操作拉 bot 进群 {gid}（{_fmt_time(rec.get('time'))}）")
        return lines[:10]

    async def _load_ban_entry(self, qq: str) -> list:
        """读取 qq_tools 黑名单中此 QQ 的条目；未装 qq_tools 或读取失败返回空。"""
        if not self.config.get("link_qq_tools_ban", True):
            return []
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
            await asyncio.sleep(self._flush_interval())
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

    # ---------------- 配置读取 ----------------

    def _quote_keep(self) -> int:
        try:
            return max(1, int(self.config.get("quote_keep", 10) or 10))
        except (TypeError, ValueError):
            return 10

    def _quote_show(self) -> int:
        try:
            return max(1, int(self.config.get("quote_show", 5) or 5))
        except (TypeError, ValueError):
            return 5

    def _max_tracked(self) -> int:
        try:
            return max(100, int(self.config.get("max_tracked_users", 5000) or 5000))
        except (TypeError, ValueError):
            return 5000

    def _flush_interval(self) -> int:
        try:
            return max(10, int(self.config.get("flush_interval", 60) or 60))
        except (TypeError, ValueError):
            return 60

    def _group_allowed(self, group_id: str) -> bool:
        """配置了采集群列表时，只采集列表内的群；留空采集全部。"""
        raw = str(self.config.get("collect_groups", "") or "")
        if not raw.strip():
            return True
        allow = {g.strip() for g in re.split(r"[,，\s]+", raw) if g.strip()}
        return group_id in allow

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

    # ---------------- 工具 ----------------

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
