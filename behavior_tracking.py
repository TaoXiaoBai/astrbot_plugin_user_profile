"""有界的按日行为轨迹；只保存聚合值，不保存额外原话。"""
from datetime import datetime


_FIELDS = ("m", "g", "p", "images", "links", "mentions", "chars", "night")


def day_number(timestamp: int) -> int:
    return datetime.fromtimestamp(int(timestamp)).date().toordinal()


def record_message(
    stats: dict,
    timestamp: int,
    *,
    group: bool,
    images: int,
    links: int,
    mentions: int,
    chars: int,
    night: bool,
    retention_days: int,
) -> None:
    """在既有批量落盘路径中更新当天聚合，不单独执行 KV 写入。"""
    day = day_number(timestamp)
    days = stats.setdefault("behavior_days", {})
    if not isinstance(days, dict):
        days = stats["behavior_days"] = {}
    entry = days.setdefault(str(day), {})
    values = {
        "m": 1, "g": int(bool(group)), "p": int(not group),
        "images": max(0, int(images)), "links": max(0, int(links)),
        "mentions": max(0, int(mentions)), "chars": max(0, int(chars)),
        "night": int(bool(night)),
    }
    for key, value in values.items():
        entry[key] = max(0, int(entry.get(key, 0) or 0)) + value
    reference_day = day
    for key in list(days):
        try:
            reference_day = max(reference_day, int(key))
        except (TypeError, ValueError):
            days.pop(key, None)
    for key in list(days):
        try:
            age = reference_day - int(key)
        except (TypeError, ValueError):
            days.pop(key, None)
            continue
        if age < -1 or age >= retention_days:
            days.pop(key, None)


def _totals(days: dict, first: int, last: int, field: str) -> tuple[int, int]:
    messages = 0
    active = 0
    for key, entry in days.items():
        try:
            day = int(key)
        except (TypeError, ValueError):
            continue
        if not first <= day <= last or not isinstance(entry, dict):
            continue
        count = max(0, int(entry.get(field, 0) or 0))
        messages += count
        active += int(count > 0)
    return messages, active


def summarize(stats: dict, now: int, scene: str = "all", window_days: int = 30) -> dict:
    """汇总当前及前一窗口；scene 只决定使用群聊或私聊计数。"""
    days = stats.get("behavior_days") if isinstance(stats.get("behavior_days"), dict) else {}
    field = {"group": "g", "private": "p"}.get(scene, "m")
    today = day_number(now)
    first = today - window_days + 1
    recent, active_days = _totals(days, first, today, field)
    previous, previous_active = _totals(days, first - window_days, first - 1, field)
    active = sorted(
        int(key) for key, entry in days.items()
        if str(key).lstrip("-").isdigit() and isinstance(entry, dict)
        and int(entry.get(field, 0) or 0) > 0 and int(key) <= today
    )
    streak = 0
    if active:
        streak = 1
        selected = set(active)
        cursor = active[-1]
        while cursor - 1 in selected:
            streak += 1
            cursor -= 1
    trend = None if previous == 0 else (recent - previous) / previous
    scope = {"group": "群聊", "private": "私聊"}.get(scene, "群私合计")
    parts = [
        f"近{window_days}天{scope}活跃{active_days}天、发言{recent}条",
        f"连续活跃{streak}天" if streak else "近期无连续活跃记录",
    ]
    if trend is not None:
        parts.append(f"较前{window_days}天{trend:+.0%}")
    elif recent:
        parts.append(f"前{window_days}天无对照发言")
    night = sum(
        max(0, int(entry.get("night", 0) or 0))
        for key, entry in days.items()
        if str(key).lstrip("-").isdigit() and first <= int(key) <= today
        and isinstance(entry, dict) and int(entry.get(field, 0) or 0) > 0
    )
    if night:
        parts.append(f"夜间{night}条")
    return {
        "text": "，".join(parts), "messages": recent, "active_days": active_days,
        "streak": streak, "previous_messages": previous,
        "previous_active_days": previous_active, "trend": trend,
    }


def memory_material(stats: dict, now: int, scene: str) -> dict | None:
    """生成仅供长期行为记忆引用的统计材料，绝不能作为用户原话或风险证据。"""
    summary = summarize(stats, now, scene)
    if not summary["messages"]:
        return None
    timestamp = int(stats.get("p_last" if scene == "private" else "g_last") or now)
    return {
        "t": timestamp, "src": "行为统计（非用户原话）", "scene": scene,
        "group_id": "", "text": "[插件按日聚合行为统计，非用户原话] " + summary["text"],
        "evidence_kind": "behavior",
    }
