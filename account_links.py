"""近期拉黑后的换号续聊线索；只做有界关联提示，不确认身份或执行处罚。"""
import math
import re


_CONTINUATION = re.compile(
    r"接续|续聊|继续(?:刚才|上次|之前|我们的|那个|这个话题|聊天)|"
    r"接着(?:刚才|上次|之前|我们的|那个|这个话题|聊)|"
    r"(?:刚才|上次|之前)(?:聊到|的话题|的话题)|接着聊|继续聊"
)
_HISTORY_RECALL = re.compile(
    r"(?:之前|以前|原先|早先).{0,30}(?:聊天|聊得|说过|头像|昵称|名字|资料)|"
    r"(?:头像|昵称|名字|资料).{0,12}(?:之前|以前|原先|早先)"
)
_ACCOUNT = re.compile(r"换(?:了个|了)?号|小号|大号|原来的号|另一个号|另外一?个号|另外一?个账号|别的号|新号|这个号")
_BAN = re.compile(r"拉黑|封禁|封号|被封|被\s*ban|黑名单", re.I)


def continuation_signals(text: str) -> list[str]:
    """识别对话接续或旧互动召回；单独的“继续”不构成关联证据。"""
    text = str(text or "").strip()
    if not text:
        return []
    signals = []
    if _CONTINUATION.search(text):
        signals.append("continuation")
    if _HISTORY_RECALL.search(text):
        signals.append("history_recall")
    if not signals:
        return []
    if _ACCOUNT.search(text):
        signals.append("account_switch")
    if _BAN.search(text):
        signals.append("ban_reference")
    return signals


def _ratio_similarity(new: dict, old: dict) -> float | None:
    new_total = int(new.get("g_count", 0) or 0) + int(new.get("p_count", 0) or 0)
    old_total = int(old.get("g_count", 0) or 0) + int(old.get("p_count", 0) or 0)
    if new_total < 10 or old_total < 10:
        return None
    values = []
    for key in ("images", "links", "mentions", "night_count"):
        a = int(new.get(key, 0) or 0) / new_total
        b = int(old.get(key, 0) or 0) / old_total
        values.append(1 - abs(a - b))
    new_chars = int(new.get("total_chars", 0) or 0) / new_total
    old_chars = int(old.get("total_chars", 0) or 0) / old_total
    if new_chars or old_chars:
        values.append(min(new_chars, old_chars) / max(new_chars, old_chars, 1))
    return sum(values) / len(values) if values else None


def detect(
    text: str,
    qq: str,
    stats: dict,
    all_stats: dict,
    ban_entries: list,
    now: int,
    group_id: str,
    window_days: int,
    active_ban,
) -> list[dict]:
    signals = continuation_signals(text)
    if not signals:
        return []
    base = 0.45 if "continuation" in signals else 0.30
    if "account_switch" in signals:
        base += 0.2
    if "ban_reference" in signals:
        base += 0.2
    if "continuation" in signals and "account_switch" in signals and "ban_reference" in signals:
        base += 0.1
    elif "history_recall" in signals and "account_switch" in signals:
        base += 0.1
    candidates = []
    for item in ban_entries:
        if not isinstance(item, dict) or item.get("temporary_safety_ban") is True:
            continue
        target = str(item.get("user_id") or "")
        if target == qq or not re.fullmatch(r"\d{5,12}", target):
            continue
        try:
            ban_time = float(item.get("ban_time", 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(ban_time) or not 0 <= now - ban_time <= window_days * 86400:
            continue
        if not active_ban(item, now):
            continue
        score = base + (0.1 if now - ban_time <= 86400 else 0.0)
        old = all_stats.get(target) if isinstance(all_stats.get(target), dict) else {}
        shared_group = bool(group_id and group_id in (old.get("groups") or {}))
        if shared_group:
            score += 0.1
        similarity = _ratio_similarity(stats, old)
        if similarity is not None:
            score += 0.1 * similarity
        score = round(min(0.95, score), 2)
        candidates.append({
            "target_qq": target, "time": int(now), "ban_time": int(ban_time),
            "group_id": str(group_id or ""), "signals": signals,
            "shared_group": shared_group,
            "behavior_similarity": round(similarity, 2) if similarity is not None else None,
            "score": score,
            "strength": "强" if score >= 0.75 else ("中" if score >= 0.6 else "弱"),
        })
    candidates.sort(key=lambda item: (item["score"], item["ban_time"]), reverse=True)
    return candidates[:5]


def merge_records(existing, incoming: list[dict]) -> list[dict]:
    """同一账号对只保留最新线索，不通过重复发言累计身份置信度。"""
    records = {}
    for item in list(existing or []) + list(incoming or []):
        if not isinstance(item, dict) or not re.fullmatch(r"\d{5,12}", str(item.get("target_qq") or "")):
            continue
        target = str(item["target_qq"])
        record = {
            "target_qq": target, "time": max(0, int(item.get("time", 0) or 0)),
            "ban_time": max(0, int(item.get("ban_time", 0) or 0)),
            "group_id": str(item.get("group_id") or "")[:32],
            "signals": [str(value)[:32] for value in item.get("signals", []) if value in (
                "continuation", "history_recall", "account_switch", "ban_reference",
            )],
            "shared_group": bool(item.get("shared_group")),
            "behavior_similarity": item.get("behavior_similarity") if type(item.get("behavior_similarity")) in (int, float) and math.isfinite(item["behavior_similarity"]) else None,
            "score": max(0.0, min(0.95, float(item.get("score", 0) or 0))),
            "strength": item.get("strength") if item.get("strength") in ("弱", "中", "强") else "弱",
        }
        if target not in records or record["time"] >= records[target]["time"]:
            records[target] = record
    return sorted(records.values(), key=lambda item: item["time"], reverse=True)[:10]


def active_records(
    records: list,
    ban_entries: list,
    now: int,
    window_days: int,
    active_ban,
) -> list[dict]:
    """旧关联目标已解封或超出窗口时，不再显示为当前黑名单关联。"""
    active_targets = set()
    for item in ban_entries:
        if not isinstance(item, dict) or item.get("temporary_safety_ban") is True:
            continue
        target = str(item.get("user_id") or "")
        try:
            ban_time = float(item.get("ban_time", 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(ban_time) and 0 <= now - ban_time <= window_days * 86400 and active_ban(item, now):
            active_targets.add(target)
    return [dict(item) for item in merge_records(records, []) if item["target_qq"] in active_targets]


def display_line(item: dict) -> str:
    signals = set(item.get("signals") or [])
    reasons = ["近期手动拉黑后出现主动续聊" if "continuation" in signals else "近期手动拉黑后主动召回旧互动/资料"]
    if "history_recall" in signals and "continuation" in signals:
        reasons.append("同时召回旧互动")
    if "account_switch" in signals:
        reasons.append("提及换号/大小号")
    if "ban_reference" in signals:
        reasons.append("提及封禁/黑名单")
    if item.get("shared_group"):
        reasons.append("有共同群")
    if item.get("behavior_similarity") is not None:
        reasons.append(f"行为统计相似度参考 {float(item['behavior_similarity']):.0%}")
    return (
        f"QQ {item['target_qq']}：{item.get('strength', '弱')}线索（规则强度 {float(item.get('score', 0)):.0%}），"
        + "、".join(reasons) + "；仅为关联线索，身份待人工核实"
    )
