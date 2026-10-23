import hashlib
import json
import math
import re
from typing import Any


LLM_SCHEMA_VERSION = 5


def quote_scene(item: dict) -> str:
    scene = item.get("scene")
    if scene in ("group", "private", "unknown"):
        return scene
    src = str(item.get("src") or "")
    if src.startswith("群 ") or src.startswith("[群 "):
        return "group"
    if src == "私聊":
        return "private"
    return "unknown"


def normalize_quote(item: dict) -> dict:
    result = dict(item)
    result["scene"] = quote_scene(item)
    if result["scene"] == "group":
        match = re.search(r"群\s+(\d+)", str(item.get("src") or ""))
        result["group_id"] = str(item.get("group_id") or (match.group(1) if match else ""))
    else:
        result["group_id"] = ""
    result["t"] = _bounded_int(item.get("t"), 0, 0, 2**63 - 1)
    return result


def quota_quotes(items: list, keep: int) -> list:
    normalized = [normalize_quote(item) for item in items if isinstance(item, dict)]
    counts = {}
    result = []
    for item in reversed(normalized):
        scene = "private" if item["scene"] == "private" else "group"
        counts[scene] = counts.get(scene, 0) + 1
        if counts[scene] <= keep:
            result.append(item)
    return list(reversed(result))


LLM_TAGS = frozenset({
    "spam_suspect", "ad_suspect", "troll", "friendly", "helpful",
    "nsfw_tendency", "political_sensitive", "scam_suspect", "repetitive",
    "normal",
})

_DEFAULTS = {
    "allow_self_query": True,
    "allow_other_query": False,
    "enable_self_shortcuts": True,
    "enable_llm_tool": True,
    "profile_ranking_enabled": True,
    "profile_ranking_page_size": 10,
    "profile_ranking_min_score": 0,
    "llm_context_inject": True,
    "llm_context_max_chars": 2000,
    "llm_context_include_management": True,
    "llm_context_include_social": True,
    "friend_reconcile_enabled": True,
    "friend_reconcile_interval": 21600,
    "card_show_tags": True,
    "card_show_stats": True,
    "card_show_impression": True,
    "card_show_traits": True,
    "card_show_social": True,
    "card_show_criminal": True,
    "store_quotes": True,
    "store_private_quotes": True,
    "llm_include_private_quotes": True,
    "cross_scene_link_enabled": True,
    "group_chat_use_linked_impression": True,
    "quote_retention_days": 0,
    "quote_keep": 10,
    "quote_show": 5,
    "max_tracked_users": 5000,
    "mute_event_keep": 100,
    "flush_interval": 60,
    "history_scan_pages": 3,
    "history_scan_page_size": 10,
    "platform_history_scan_limit": 700,
    "history_scan_cooldown": 3600,
    "history_rescan_interval": 86400,
    "history_scan_batch_limit": 200,
    "history_scan_concurrency": 4,
    "silent_for_banned": True,
    "llm_tag_cache_ttl": 86400,
    "llm_failure_cache_ttl": 300,
    "llm_refresh_interval": 3600,
    "llm_timeout_seconds": 45,
    "llm_max_concurrency": 3,
    "llm_material_max_chars": 6000,
    "tag_active_high_threshold": 100,
    "tag_active_med_threshold": 20,
    "tag_newcomer_days": 7,
    "tag_multi_group_threshold": 3,
    "tag_image_threshold": 0.5,
    "tag_link_threshold": 0.3,
    "tag_mention_threshold": 0.3,
    "tag_verbose_threshold": 80,
    "tag_night_threshold": 0.3,
    "tag_behavior_min_samples": 10,
    "tag_qr_min_count": 2,
    "risk_semantic_cap": 60,
    "risk_behavior_cap": 15,
    "management_resolution_decay_days": 30,
    "management_appeal_clear_history": True,
    "risk_level_low": 30,
    "risk_level_high": 60,
    "risk_level_extreme": 80,
}

_INT_RULES = {
    "profile_ranking_page_size": (1, 50),
    "profile_ranking_min_score": (0, 100),
    "quote_retention_days": (0, 3650),
    "quote_keep": (1, 100),
    "quote_show": (1, 50),
    "max_tracked_users": (100, 100000),
    "mute_event_keep": (1, 500),
    "llm_context_max_chars": (500, 8000),
    "friend_reconcile_interval": (300, 2592000),
    "flush_interval": (10, 3600),
    "history_scan_pages": (1, 100),
    "history_scan_page_size": (1, 100),
    "platform_history_scan_limit": (50, 5000),
    "history_scan_cooldown": (0, 86400),
    "history_rescan_interval": (0, 2592000),
    "history_scan_batch_limit": (1, 1000),
    "history_scan_concurrency": (1, 20),
    "llm_tag_cache_ttl": (0, 2592000),
    "llm_failure_cache_ttl": (0, 86400),
    "llm_refresh_interval": (0, 2592000),
    "llm_timeout_seconds": (1, 300),
    "llm_max_concurrency": (1, 20),
    "llm_material_max_chars": (500, 20000),
    "tag_active_high_threshold": (1, 1000000),
    "tag_active_med_threshold": (1, 1000000),
    "tag_newcomer_days": (0, 3650),
    "tag_multi_group_threshold": (1, 10000),
    "tag_verbose_threshold": (1, 100000),
    "tag_behavior_min_samples": (1, 10000),
    "tag_qr_min_count": (1, 1000),
    "risk_semantic_cap": (0, 100),
    "risk_behavior_cap": (0, 100),
    "management_resolution_decay_days": (0, 3650),
    "risk_level_low": (0, 100),
    "risk_level_high": (0, 100),
    "risk_level_extreme": (0, 100),
}

_FLOAT_RULES = {
    "tag_image_threshold": (0.0, 1.0),
    "tag_link_threshold": (0.0, 1.0),
    "tag_mention_threshold": (0.0, 1.0),
    "tag_night_threshold": (0.0, 1.0),
}


def _finite_number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    number = int(_finite_number(value, default))
    return max(low, min(high, number))


def normalize_config(config: dict | None) -> dict:
    source = config or {}
    flattened = dict(source)
    for values in source.values():
        if isinstance(values, dict):
            for key, value in values.items():
                flattened.setdefault(key, value)

    if "allow_self_query" not in flattened:
        flattened["allow_self_query"] = bool(flattened.get("enable_self_command", True))
    if "allow_other_query" not in flattened:
        if bool(flattened.get("self_query_only", False)):
            flattened["allow_other_query"] = False
        else:
            flattened["allow_other_query"] = bool(flattened.get("public_query", False))
    if "enable_self_shortcuts" not in flattened:
        flattened["enable_self_shortcuts"] = bool(flattened.get("enable_self_command", True))

    for key, default in _DEFAULTS.items():
        flattened.setdefault(key, default)
    for key, (low, high) in _INT_RULES.items():
        flattened[key] = _bounded_int(flattened.get(key), _DEFAULTS[key], low, high)
    for key, (low, high) in _FLOAT_RULES.items():
        value = _finite_number(flattened.get(key), _DEFAULTS[key])
        flattened[key] = max(low, min(high, value))

    medium = flattened["tag_active_med_threshold"]
    flattened["tag_active_high_threshold"] = max(
        medium, flattened["tag_active_high_threshold"]
    )
    levels = sorted((
        flattened["risk_level_low"],
        flattened["risk_level_high"],
        flattened["risk_level_extreme"],
    ))
    flattened["risk_level_low"], flattened["risk_level_high"], flattened["risk_level_extreme"] = levels
    return flattened


def clean_text(value: Any, limit: int) -> str:
    text = str(value or "")
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    text = re.sub(r"[\r\n\t ]+", " ", text).strip()
    return text[:limit]


def sanitize_llm_tags(raw: Any) -> list[dict]:
    if isinstance(raw, dict):
        raw = raw.get("tags")
    if not isinstance(raw, list):
        return []
    result = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        tag = clean_text(item.get("tag"), 64).lower()
        if tag not in LLM_TAGS or tag in seen:
            continue
        confidence = _finite_number(item.get("confidence"), -1)
        if confidence < 0:
            continue
        seen.add(tag)
        result.append({
            "tag": tag,
            "confidence": round(max(0.0, min(1.0, confidence)), 2),
            "source": "llm",
            "evidence": clean_text(item.get("reason", item.get("evidence")), 160),
        })
        if len(result) >= 5:
            break
    return result


def sanitize_llm_analysis(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {"tags": sanitize_llm_tags(raw), "impression": "", "traits": []}
    impression = clean_text(raw.get("impression", raw.get("summary")), 240)
    traits_raw = raw.get("traits")
    traits = []
    seen = set()
    if isinstance(traits_raw, list):
        for value in traits_raw:
            if isinstance(value, dict):
                value = value.get("trait", value.get("name", ""))
            trait = clean_text(value, 48)
            key = trait.casefold()
            if not trait or key in seen:
                continue
            seen.add(key)
            traits.append(trait)
            if len(traits) >= 5:
                break
    return {
        "tags": sanitize_llm_tags(raw.get("tags")),
        "impression": impression,
        "traits": traits,
    }


def build_material_fingerprint(
    quotes: list[dict],
    provider: str,
    schema: int,
    stats: dict | None = None,
    base_tags: list[dict] | None = None,
) -> str:
    stats = stats if isinstance(stats, dict) else {}
    signal_keys = (
        "g_count", "p_count", "platform_history_count", "images", "links",
        "qrs", "mentions", "total_chars", "night_count",
    )
    signals = {
        key: _bounded_int(stats.get(key), 0, 0, 2**63 - 1)
        for key in signal_keys
    }
    normalized_tags = []
    for item in base_tags if isinstance(base_tags, list) else []:
        if not isinstance(item, dict):
            continue
        normalized_tags.append({
            "tag": clean_text(item.get("tag"), 64),
            "confidence": _finite_number(item.get("confidence"), 0.0),
            "source": clean_text(item.get("source"), 32),
        })
        if len(normalized_tags) >= 8:
            break
    material = {
        "provider": str(provider or ""),
        "schema": int(schema),
        "quotes": [
            {"src": str(item.get("src") or ""), "text": str(item.get("text") or ""),
             "scene": quote_scene(item), "group_id": str(item.get("group_id") or ""),
             "t": str(item.get("t") or "0")}
            for item in quotes
            if isinstance(item, dict)
        ],
        "signals": signals,
        "base_tags": normalized_tags,
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def speaker_lines(text: str, qq: str) -> list[str]:
    pattern = re.compile(
        r"^\s*(?:\[[^\]]*\]\s*)?[^\n]*?\(ID:\s*" + re.escape(str(qq)) + r"\s*\)\s*[:：]\s*(.*)$"
    )
    result = []
    for raw_line in str(text or "").splitlines():
        match = pattern.match(raw_line)
        if match:
            line = clean_text(re.sub(r"^\s*\[At:[^\]]*\]\s*", "", match.group(1)), 200)
            if len(line) >= 2:
                result.append(line)
    return result
