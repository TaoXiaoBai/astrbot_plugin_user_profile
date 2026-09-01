import asyncio
import inspect
import json
import os
import re
import textwrap
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

try:
    from PIL import Image as PILImage, ImageDraw, ImageFont

    HAS_PIL = True
except Exception:
    HAS_PIL = False


# 加群邀请守卫的 plugin_id（author/name 全小写，见 StarMetadata.plugin_id）
INVITE_GUARD_STAR_NAME = "astrbot_plugin_group_invite_guard"
INVITE_GUARD_PLUGIN_ID = "kimi/astrbot_plugin_group_invite_guard"
QQ_TOOLS_STAR_NAME = "astrbot_plugin_qq_tools"

_QUOTE_MAX_LEN = 200  # 单条原话最长保留字符数
_HISTORY_QUOTE_KEEP = 10  # 会话历史补充原话的最大条数

_STATS_KEY = "up_stats"
_QUOTES_KEY = "up_quotes"
_TAGS_KEY = "up_tags"

# 标签人类可读名（对 LLM 和人类都友好）
_TAG_DISPLAY_NAMES = {
    "active_high": "高活跃",
    "active_medium": "较活跃",
    "active_low": "低活跃",
    "newcomer": "新人",
    "long_inactive": "长期沉寂",
    "multi_group": "多群出现",
    "private_active": "私聊活跃",
    "ban_history": "黑名单记录",
    "kick_history": "拉群前科",
    "mute_history": "禁言前科",
    "frequent_inviter": "频繁邀请",
    "inviter": "曾邀请进群",
    "invite_rejected": "邀请被拒",
    "image_spammer": "图片刷屏",
    "link_spammer": "链接刷屏",
    "qr_spammer": "二维码刷屏",
    "mention_heavy": "频繁@人",
    "verbose": "话痨",
    "night_active": "夜间活跃",
    "spam_suspect": "刷屏嫌疑",
    "ad_suspect": "广告嫌疑",
    "troll": "抬杠/钓鱼",
    "friendly": "语气友好",
    "helpful": "乐于助人",
    "nsfw_tendency": "不适宜内容",
    "political_sensitive": "敏感倾向",
    "scam_suspect": "诈骗嫌疑",
    "repetitive": "重复内容",
    "normal": "表现正常",
}

# 默认风险权重：正数为风险，负数为信任
_RISK_WEIGHTS_DEFAULT = {
    "ban_history": 40,
    "invite_rejected": 25,
    "kick_history": 25,
    "mute_history": 20,
    "frequent_inviter": 20,
    "ad_suspect": 20,
    "scam_suspect": 20,
    "spam_suspect": 15,
    "troll": 15,
    "nsfw_tendency": 15,
    "political_sensitive": 15,
    "qr_spammer": 15,
    "link_spammer": 10,
    "image_spammer": 10,
    "newcomer": 5,
    "private_active": 5,
    "night_active": 5,
    "active_high": -5,
    "active_medium": -5,
    "helpful": -10,
    "friendly": -10,
    "normal": -15,
}


def _tag_display(tag: str) -> str:
    return _TAG_DISPLAY_NAMES.get(tag, tag)


def _risk_level(score: int, cfg: dict) -> str:
    if score >= int(cfg.get("risk_level_extreme", 80) or 80):
        return "极高"
    if score >= int(cfg.get("risk_level_high", 60) or 60):
        return "高"
    if score >= int(cfg.get("risk_level_low", 30) or 30):
        return "中"
    return "低"


def _risk_color(score: int) -> str:
    if score >= 80:
        return "#d32f2f"
    if score >= 60:
        return "#f57c00"
    if score >= 30:
        return "#fbc02d"
    return "#388e3c"


class TagEngine:
    """标签生成引擎：把原始统计、原话、前科转换成结构化标签。"""

    def __init__(self, config: dict):
        self.config = config or {}

    def generate_base_tags(self, qq: str, st: dict, quotes: list, guard_records: dict, ban_lines: list) -> list[dict]:
        """零 LLM、零网络，从统计和行为规则里秒出基础标签。"""
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

        # 内容/行为信号标签
        if total > 0:
            images = int(st.get("images") or 0)
            links = int(st.get("links") or 0)
            qrs = int(st.get("qrs") or 0)
            mentions = int(st.get("mentions") or 0)
            total_chars = int(st.get("total_chars") or 0)
            night_count = int(st.get("night_count") or 0)

            img_ratio = images / total
            link_ratio = links / total
            mention_ratio = mentions / total
            night_ratio = night_count / total
            avg_len = total_chars / total

            img_th = float(self.config.get("tag_image_threshold", 0.5) or 0.5)
            link_th = float(self.config.get("tag_link_threshold", 0.3) or 0.3)
            mention_th = float(self.config.get("tag_mention_threshold", 0.3) or 0.3)
            verbose_th = float(self.config.get("tag_verbose_threshold", 80) or 80)
            night_th = float(self.config.get("tag_night_threshold", 0.3) or 0.3)

            if img_ratio >= img_th:
                tags.append(self._tag("image_spammer", round(min(0.5 + img_ratio, 0.95), 2), "stats", f"图片消息占比 {img_ratio:.0%}"))
            if link_ratio >= link_th:
                tags.append(self._tag("link_spammer", round(min(0.5 + link_ratio, 0.95), 2), "stats", f"含链接消息 {links} 条"))
            if qrs > 0:
                tags.append(self._tag("qr_spammer", 0.75, "stats", f"检测到二维码相关内容 {qrs} 次"))
            if mention_ratio >= mention_th:
                tags.append(self._tag("mention_heavy", round(min(0.5 + mention_ratio, 0.95), 2), "stats", f"含@消息 {mentions} 条"))
            if avg_len >= verbose_th:
                tags.append(self._tag("verbose", 0.7, "stats", f"平均消息长度 {avg_len:.0f} 字"))
            if night_ratio >= night_th:
                tags.append(self._tag("night_active", round(min(0.5 + night_ratio, 0.9), 2), "stats", f"夜间发言 {night_count} 条"))

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
        base_desc = ", ".join(f"{_tag_display(t['tag'])}({t['confidence']})" for t in base_tags[:8]) or "无"

        # 行为信号补充
        signals = []
        total = int(st.get("g_count") or 0) + int(st.get("p_count") or 0)
        if total > 0:
            signals.append(f"图片消息占比 {int(st.get('images') or 0) / total:.0%}")
            signals.append(f"含链接消息占比 {int(st.get('links') or 0) / total:.0%}")
            signals.append(f"含@消息占比 {int(st.get('mentions') or 0) / total:.0%}")
            signals.append(f"夜间发言占比 {int(st.get('night_count') or 0) / total:.0%}")
        signal_text = "；".join(signals) if signals else "无额外信号"

        prompt = (
            f"你正在为一个 QQ 用户打标签，用于辅助判断此人进群是否有风险。\n"
            f"QQ: {qq}\n"
            f"基础统计标签：{base_desc}\n"
            f"行为信号：{signal_text}\n"
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
        "images": 0,
        "links": 0,
        "qrs": 0,
        "mentions": 0,
        "total_chars": 0,
        "night_count": 0,
    }


@register(
    "astrbot_plugin_user_profile",
    "Kimi",
    "QQ 用户画像 / 自动标签引擎：被动采集群聊与私聊发言，自动打上活跃度、风险、社交、内容等结构化标签，输出综合风险分，支持细粒度查询权限，供加群邀请守卫等插件决策调用",
    "1.3.0",
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
        self._tag_lock = asyncio.Lock()  # 防止并发查询重复调 LLM
        self._tag_engine = TagEngine(self.config)

    def _enabled(self) -> bool:
        return bool(self.config.get("enable", True))

    def _check_query_permission(self, target_qq: str, event: AstrMessageEvent) -> tuple[bool, str]:
        """统一查询权限判断。返回 (是否允许, 拒绝原因)。管理员始终允许。"""
        target_qq = str(target_qq or "").strip()
        sender = str(event.get_sender_id() or "").strip()

        # 1. 管理员放行
        try:
            if event.is_admin():
                return True, ""
        except Exception:
            pass

        group_id = str(event.get_group_id() or "").strip()

        # 2. 指定公开群全员可查
        raw_groups = str(self.config.get("group_public_query_groups", "") or "").strip()
        if raw_groups and group_id:
            allowed = {g.strip() for g in re.split(r"[,，\s]+", raw_groups) if g.strip()}
            if group_id in allowed:
                return True, ""

        # 3. 自查询快捷命令放行：/我的画像 /查自己 /我 /画像 自己
        if sender and sender == target_qq and self.config.get("enable_self_command", True):
            return True, ""

        # 4. 仅允许查自己
        if self.config.get("self_query_only", False):
            if sender and sender == target_qq:
                return True, ""
            return False, "当前仅允许查询自己的画像（管理员除外）。"

        # 5. 全局公开查询
        if self.config.get("public_query", True):
            return True, ""

        return False, "当前仅管理员可查询用户画像。"

    def _llm_tag_cache_ttl(self) -> int:
        try:
            raw = self.config.get("llm_tag_cache_ttl")
            if raw is None:
                return 86400
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 86400

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
        hour = datetime.fromtimestamp(now).hour

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

            # 内容信号统计
            if text:
                st["total_chars"] = int(st.get("total_chars") or 0) + len(text)
                st["links"] = int(st.get("links") or 0) + len(re.findall(r"https?://\S+|www\.\S+", text))
                st["qrs"] = int(st.get("qrs") or 0) + (1 if re.search(r"二维码|qr.?code|qrcode", text, re.I) else 0)
                st["mentions"] = int(st.get("mentions") or 0) + len(re.findall(r"[@＠]\w+", text))
            else:
                # 空文本大概率是图片/表情/文件等富媒体
                st["images"] = int(st.get("images") or 0) + 1

            # 夜间活跃：0-5 点
            if 0 <= hour < 6:
                st["night_count"] = int(st.get("night_count") or 0) + 1

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
        logger.info(f"user_profile: /画像 triggered, text={text!r}")

        # 兜底：如果 AstrBot 把 /我的画像 /查自己 路由到 /画像，转去查自己
        if text in ("我的画像", "查自己"):
            logger.info("user_profile: routing self-like command to self profile")
            await self._send_self_profile(event)
            return

        match = re.search(r"\d{5,12}", text)
        if not match:
            await event.send(MessageChain(chain=[Plain("用法：/画像 <QQ号>")]))
            return
        qq = match.group(0)

        allowed, reason = self._check_query_permission(qq, event)
        if not allowed:
            await event.send(MessageChain(chain=[Plain(reason)]))
            return

        await self._send_profile(qq, event)

    @filter.command("我")
    async def self_profile_command_short(self, event: AstrMessageEvent):
        """快捷命令 /我：查自己的画像（最不容易被 AstrBot 命令解析器吞掉）。"""
        logger.info("user_profile: /我 triggered")
        await self._send_self_profile(event)

    @filter.command("我的画像", alias=["查自己"])
    async def self_profile_command(self, event: AstrMessageEvent):
        logger.info("user_profile: /我的画像 triggered")
        await self._send_self_profile(event)

    @filter.regex(r"^/(?:画像|我的画像|查自己|我)")
    async def slash_command_fallback(self, event: AstrMessageEvent):
        """兜底：当 AstrBot 的 wake_prefix 不是 '/' 时，直接以 '/' 开头的命令
        不会被标准 command filter 捕获。此正则在所有平台统一处理 /画像 /我的画像
        /查自己 /我，并避开 wake_prefix='/' 时已由 command filter 处理的情况。"""
        if not self._enabled():
            return
        raw = (event.get_message_str() or "").strip()
        # 如果 AstrBot 已经通过标准 command 唤醒，且剥离 wake_prefix 后不再以
        # '/' 开头，说明 command handler 会处理，这里不再重复响应。
        if event.is_at_or_wake_command and not raw.startswith("/"):
            return

        logger.info(f"user_profile: slash fallback triggered, raw={raw!r}")

        # /我的画像 /查自己 /我 -> 查自己
        if raw == "/我的画像" or raw == "/查自己" or raw == "/我":
            await self._send_self_profile(event)
            event.stop_event()
            return

        # /画像 ...
        if raw.startswith("/画像"):
            rest = raw[len("/画像"):].strip()
            # /画像 自己 /画像 我 /画像 me /画像 不带参数 -> 查自己
            if not rest or rest.lower() in ("自己", "我", "me"):
                await self._send_self_profile(event)
                event.stop_event()
                return
            match = re.search(r"\d{5,12}", rest)
            if not match:
                await event.send(MessageChain(chain=[Plain("用法：/画像 <QQ号>")]))
                event.stop_event()
                return
            qq = match.group(0)
            allowed, reason = self._check_query_permission(qq, event)
            if not allowed:
                await event.send(MessageChain(chain=[Plain(reason)]))
                event.stop_event()
                return
            await self._send_profile(qq, event)
            event.stop_event()
            return

    async def _send_self_profile(self, event: AstrMessageEvent):
        """查询并发送发送者自己的画像。"""
        if not self._enabled():
            logger.info("user_profile: self profile skipped, plugin disabled")
            return
        if not self.config.get("enable_self_command", True):
            logger.info("user_profile: self profile skipped, enable_self_command=false")
            return
        sender = str(event.get_sender_id() or "").strip()
        logger.info(f"user_profile: self profile sender={sender!r}")
        if not re.fullmatch(r"\d{5,12}", sender):
            await event.send(MessageChain(chain=[Plain("无法获取你的 QQ 号")]))
            return
        await self._send_profile(sender, event)

    async def _send_profile(self, qq: str, event: AstrMessageEvent):
        """查询并发送画像的公共逻辑。"""
        logger.info(f"user_profile: _send_profile qq={qq!r}")
        try:
            profile = await self._build_profile_text(qq, event)
        except Exception as exc:
            logger.error(f"user_profile: _build_profile_text failed: {exc}")
            await event.send(MessageChain(chain=[Plain(f"生成画像失败：{exc}")]))
            return
        chain = await self._render_message_chain(qq, profile)
        try:
            await event.send(MessageChain(chain=chain))
        except Exception as exc:
            logger.error(f"user_profile: send profile failed: {exc}")
            try:
                await event.send(MessageChain(chain=[Plain(profile)]))
            except Exception as exc2:
                logger.error(f"user_profile: fallback send failed: {exc2}")

    @filter.llm_tool(name="user_profile_query")
    async def user_profile_query(self, event, qq: str):
        """查询指定 QQ 号用户的画像标签与综合风险分：活跃度、风险标签、LLM 语义标签。用于判断该用户是否可信、是否适合进群。需要了解某个 QQ 用户的背景时调用。"""
        event = _unwrap_event(event)
        if not self._enabled():
            return "用户画像插件当前未启用。"
        qq = str(qq or "").strip()
        if not re.fullmatch(r"\d{5,12}", qq):
            return "查询失败：请提供 5-12 位纯数字 QQ 号。"
        result = await self.get_profile_tags_with_score(qq, event)
        tags = result.get("tags") or []
        if not tags:
            return f"QQ {qq} 暂无画像记录。"
        lines = [
            f"QQ {qq} 的综合风险分：{result['score']}（{result['level']}）",
            "标签：",
        ]
        for t in tags:
            lines.append(f"- {_tag_display(t['tag'])}（置信度 {t['confidence']}，来源 {t['source']}）{t.get('evidence', '')}")
        return "\n".join(lines)

    # ---------------- 画像组装 ----------------

    async def _build_profile_text(self, qq: str, event) -> str:
        """给 /画像 命令用：返回人类可读文本。"""
        result = await self.get_profile_tags_with_score(qq, event)
        tags = result.get("tags") or []
        if not tags:
            return f"【用户画像】QQ {qq}\n暂无记录：未采集到该用户的发言，也没有前科数据。"

        await self._ensure_loaded()

        base = [t for t in tags if t["source"] != "llm"]
        llm = [t for t in tags if t["source"] == "llm"]

        lines = [f"【用户画像】QQ {qq}"]

        # 昵称
        stranger = await self._fetch_stranger_info(qq, event)
        nickname = str((stranger or {}).get("nickname") or "").strip()
        if nickname:
            lines[0] += f"（{nickname}）"

        # 风险分
        lines.append(f"综合风险分：{result['score']} / 100（{result['level']}）")

        # 基础标签
        if base:
            tag_line = " | ".join(
                f"{_tag_display(t['tag'])}({int(t['confidence'] * 100)}%)" for t in base
            )
            lines.append(f"基础标签：{tag_line}")

        # LLM 标签
        if llm:
            tag_line = " | ".join(
                f"{_tag_display(t['tag'])}({int(t['confidence'] * 100)}%)" for t in llm
            )
            lines.append(f"LLM 标签：{tag_line}")

        # 活跃度
        stats = self._stats.get(qq) or {}
        activity = self._format_activity(stats)
        if activity:
            lines.append(activity)

        # 最近原话（可开关）
        if self.config.get("show_quotes", True):
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

    async def _render_message_chain(self, qq: str, profile_text: str) -> list:
        """根据配置返回文本或图片消息链。"""
        if not self.config.get("image_output", False):
            return [Plain(profile_text)]
        if not HAS_PIL:
            return [Plain(profile_text + "\n\n[图片输出未启用：缺少 Pillow 依赖]")]
        path = self._render_profile_image(qq, profile_text)
        if path:
            logger.info(f"user_profile: sending image output {path}")
            return [Image.fromFileSystem(path)]
        logger.warning("user_profile: image render returned None, fallback to text")
        return [Plain(profile_text)]

    def _render_profile_image(self, qq: str, text: str) -> str | None:
        """把画像文本渲染成图片，返回临时文件路径。优化：按像素宽度折行，支持 CJK，标签自动换行。"""
        if not HAS_PIL:
            return None
        try:
            width = 900
            padding = 40
            line_height = 34
            title_height = 80
            content_width = width - padding * 2

            # 字体
            font_paths = [
                "C:/Windows/Fonts/msyh.ttc",
                "C:/Windows/Fonts/simhei.ttf",
                "C:/Windows/Fonts/simsun.ttc",
                "C:/Windows/Fonts/msyhbd.ttc",
            ]
            font = None
            for fp in font_paths:
                try:
                    font = ImageFont.truetype(fp, 24)
                    break
                except Exception:
                    continue
            if font is None:
                font = ImageFont.load_default()
            try:
                title_font = ImageFont.truetype(font_paths[0], 32) if font_paths else ImageFont.load_default()
            except Exception:
                title_font = font

            def _text_size(s: str, f) -> tuple:
                """兼容新旧 Pillow 的文本尺寸计算。"""
                try:
                    bbox = draw.textbbox((0, 0), s, font=f)
                    return bbox[2] - bbox[0], bbox[3] - bbox[1]
                except Exception:
                    try:
                        return draw.textsize(s, font=f)
                    except Exception:
                        return len(s) * 12, 24

            def _is_wide(c: str) -> bool:
                """粗略判断字符是否占两个英文字符宽度（CJK 等）。"""
                o = ord(c)
                # CJK 统一表意符号及其扩展
                if 0x4E00 <= o <= 0x9FFF:
                    return True
                if 0x3400 <= o <= 0x4DBF:
                    return True
                if 0x3040 <= o <= 0x309F or 0x30A0 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7AF:
                    return True
                if 0xFF01 <= o <= 0xFF60:
                    return True
                return False

            def _display_width(s: str) -> int:
                return sum(2 if _is_wide(c) else 1 for c in s)

            def _wrap_by_width(s: str, max_dw: int) -> list[str]:
                """按显示宽度折行，优先在标点/空格处断开。"""
                if not s:
                    return []
                lines_out = []
                cur = ""
                cur_dw = 0
                for c in s:
                    cw = 2 if _is_wide(c) else 1
                    if cur_dw + cw > max_dw and cur:
                        # 尝试回退到最近的断点
                        cut = -1
                        for idx in range(len(cur) - 1, max(0, len(cur) - 12), -1):
                            if cur[idx] in " ，,.。！？、；：" " \\t":
                                cut = idx + 1
                                break
                        if cut > 0:
                            lines_out.append(cur[:cut])
                            cur = cur[cut:]
                            cur_dw = _display_width(cur)
                        else:
                            lines_out.append(cur)
                            cur = ""
                            cur_dw = 0
                    cur += c
                    cur_dw += cw
                if cur:
                    lines_out.append(cur)
                return lines_out

            # 估算 24px 字体下单个窄字符宽度（msyh 约 12px），从而得到目标显示宽度
            sample_w, _ = _text_size("a", font)
            if sample_w <= 0:
                sample_w = 12
            max_dw = max(20, int(content_width / sample_w))

            # 预排版：把所有行转成渲染单元，并计算总高度
            render_items = []
            for raw_line in text.splitlines():
                if raw_line.startswith("综合风险分："):
                    render_items.append(("score", raw_line))
                elif raw_line.startswith("基础标签：") or raw_line.startswith("LLM 标签："):
                    render_items.append(("tags", raw_line))
                else:
                    sub_lines = _wrap_by_width(raw_line, max_dw)
                    for sub in sub_lines:
                        render_items.append(("plain", sub))

            height = max(500, title_height + len(render_items) * line_height + padding * 2)

            img = PILImage.new("RGB", (width, height), color=(250, 250, 250))
            draw = ImageDraw.Draw(img)

            # 标题背景
            draw.rectangle([(0, 0), (width, title_height)], fill=(33, 150, 243))
            draw.text((padding, 20), f"用户画像  QQ {qq}", fill=(255, 255, 255), font=title_font)

            y = title_height + padding
            for kind, content in render_items:
                if y + line_height > height - padding:
                    # 内容超长时追加提示并停止
                    draw.text((padding, y), "... 内容过多，已截断 ...", fill=(150, 150, 150), font=font)
                    break

                if kind == "score":
                    color = (33, 33, 33)
                    score_match = re.search(r"(\d+)\s*/\s*100", content)
                    if score_match:
                        color = _risk_color(int(score_match.group(1)))
                    draw.text((padding, y), content, fill=color, font=font)
                    y += line_height
                    continue

                if kind == "tags":
                    prefix = content.split("：")[0] + "："
                    draw.text((padding, y), prefix, fill=(66, 66, 66), font=font)
                    pw, _ = _text_size(prefix, font)
                    x = padding + pw + 8
                    rest = content[len(prefix):]
                    tag_parts = rest.split(" | ") if rest else []
                    row_y = y
                    for part in tag_parts:
                        tag_name = part.split("(")[0]
                        tw, th = _text_size(tag_name, font)
                        # 标签本身超长时按显示宽度截断
                        if tw > content_width - padding:
                            sub_tags = _wrap_by_width(tag_name, max_dw)
                            tag_name = sub_tags[0] + "…" if sub_tags else "…"
                            tw, th = _text_size(tag_name, font)
                        if x + tw + 16 > width - padding:
                            x = padding
                            row_y += line_height + 6
                        try:
                            draw.rounded_rectangle([(x - 4, row_y - 2), (x + tw + 8, row_y + th + 6)], radius=6, fill=(225, 245, 254))
                        except Exception:
                            draw.rectangle([(x - 4, row_y - 2), (x + tw + 8, row_y + th + 6)], fill=(225, 245, 254))
                        draw.text((x, row_y), tag_name, fill=(2, 119, 189), font=font)
                        x += tw + 24
                    y = row_y + line_height
                    continue

                # 普通文本
                draw.text((padding, y), content, fill=(33, 33, 33), font=font)
                y += line_height

            tmp_dir = os.path.join(os.path.dirname(__file__), "tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            path = os.path.join(tmp_dir, f"profile_{qq}_{int(time.time())}.png")
            img.save(path, "PNG")
            return path
        except Exception as exc:
            logger.warning(f"user_profile: render image failed: {exc}")
            return None

    # ---------------- 对外 API（供加群邀请守卫等插件读取标签） ----------------

    async def get_profile_tags_with_score(self, qq: str, event=None) -> dict:
        """返回标签列表 + 综合风险分 + 风险等级。"""
        tags = await self.get_profile_tags(qq, event)
        score = self._calc_risk_score(tags)
        return {"score": score, "level": _risk_level(score, self.config), "tags": tags}

    async def get_profile_tags(self, qq: str, event=None) -> list[dict]:
        """返回指定 QQ 的结构化标签列表，供加群邀请守卫等插件在决策时调用。

        调用方式（以加群邀请守卫为例）::

            md = self.context.get_registered_star("astrbot_plugin_user_profile")
            instance = getattr(md, "star_cls", None)
            if instance is not None and hasattr(instance, "get_profile_tags"):
                tags = await instance.get_profile_tags(inviter_qq, event)
                # 或带风险分
                result = await instance.get_profile_tags_with_score(inviter_qq, event)

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

    async def get_risk_score(self, qq: str, event=None) -> int:
        """返回指定 QQ 的综合风险分（0-100）。"""
        result = await self.get_profile_tags_with_score(qq, event)
        return result.get("score", 0)

    def _calc_risk_score(self, tags: list) -> int:
        """根据标签和权重计算综合风险分。"""
        weights = self._risk_weights()
        score = 0.0
        for t in tags:
            w = weights.get(t["tag"], 0)
            score += w * float(t.get("confidence") or 0)
        # 以 50 为中性基准，正负权重在此基础上波动
        score = 50 + score
        return max(0, min(100, int(score)))

    def _risk_weights(self) -> dict:
        """读取风险权重配置，JSON 字符串或 dict。"""
        raw = self.config.get("risk_weights", "")
        if isinstance(raw, dict):
            merged = dict(_RISK_WEIGHTS_DEFAULT)
            merged.update(raw)
            return merged
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    merged = dict(_RISK_WEIGHTS_DEFAULT)
                    merged.update(parsed)
                    return merged
            except Exception as exc:
                logger.warning(f"user_profile: risk_weights parse failed: {exc}")
        return dict(_RISK_WEIGHTS_DEFAULT)

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

        ttl = self._llm_tag_cache_ttl()

        def _cached_tags() -> list:
            if not isinstance(cached, dict):
                return []
            if cached.get("count") != total:
                return []
            if ttl == 0:
                # 0 表示关闭缓存，每次重新生成
                return []
            if ttl > 0:
                cached_time = int(cached.get("time") or 0)
                if cached_time and int(time.time()) - cached_time > ttl:
                    return []
            return list(cached.get("tags") or [])

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
        total = g + p
        if total > 0:
            lines.append(f"- 图片/链接/@：{st.get('images', 0)} / {st.get('links', 0)} / {st.get('mentions', 0)}")
            lines.append(f"- 夜间发言：{st.get('night_count', 0)} 条")
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
