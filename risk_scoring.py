"""Pure, non-negative risk scoring for user-profile evidence.

The score is an attention indicator, not a calibrated probability.  Missing
evidence is represented by the caller with ``None``; this module only scores
actual tags and mute-account evidence.
"""

import math
from typing import Any


TAG_CATEGORIES = {
    "ban_history": "verified",
    "astrbot_banned": "verified",
    "bot_deleted_friend": "verified",
    "scam_suspect": "semantic",
    "ad_suspect": "semantic",
    "spam_suspect": "semantic",
    "troll": "semantic",
    "nsfw_tendency": "semantic",
    "political_sensitive": "semantic",
    "qr_spammer": "behavior",
    "link_spammer": "behavior",
    "image_spammer": "behavior",
    # Neutral by default, but still bounded as behavior if an administrator
    # deliberately assigns a positive custom weight.
    "newcomer": "behavior",
    "private_active": "behavior",
    "night_active": "behavior",
    "frequent_inviter": "behavior",
    "invite_rejected": "behavior",
    "kick_history": "behavior",
    "friend_relation_missing": "behavior",
}


DEFAULT_WEIGHTS = {
    "ban_history": 40.0,
    "astrbot_banned": 30.0,
    "bot_deleted_friend": 25.0,
    "scam_suspect": 45.0,
    "ad_suspect": 25.0,
    "spam_suspect": 20.0,
    "troll": 15.0,
    "nsfw_tendency": 15.0,
    "political_sensitive": 10.0,
    "qr_spammer": 10.0,
    "link_spammer": 5.0,
    "image_spammer": 5.0,
    # These remain useful descriptive tags, not risk evidence by default.
    "invite_rejected": 0.0,
    "kick_history": 0.0,
    "frequent_inviter": 0.0,
    "newcomer": 0.0,
    "private_active": 0.0,
    "night_active": 0.0,
    "active_high": 0.0,
    "active_medium": 0.0,
    "helpful": 0.0,
    "friendly": 0.0,
    "normal": 0.0,
    "friend_relation_missing": 0.0,
    "friend_relation_restored": 0.0,
    "bot_mute_operator": 0.0,
    "bot_mute_associated": 0.0,
}


def finite_number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def bounded_int(value: Any, default: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(finite_number(value, default))))


def merged_weights(custom: Any = None) -> dict[str, float]:
    result = dict(DEFAULT_WEIGHTS)
    if not isinstance(custom, dict):
        return result
    for tag, value in custom.items():
        if tag not in result or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(number):
            continue
        # Negative trust offsets are deliberately no longer supported.
        result[tag] = max(0.0, number)
    return result


def unique_confidences(tags: Any) -> dict[str, float]:
    unique: dict[str, float] = {}
    for item in tags if isinstance(tags, list) else []:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "")
        if not tag:
            continue
        confidence = max(0.0, min(1.0, finite_number(item.get("confidence"), 0.0)))
        unique[tag] = max(unique.get(tag, 0.0), confidence)
    return unique


def calculate_tag_components(
    tags: Any,
    weights: dict[str, float] | None = None,
    semantic_cap: int = 60,
    behavior_cap: int = 15,
) -> dict[str, int]:
    weights = weights or DEFAULT_WEIGHTS
    caps = {
        "semantic": bounded_int(semantic_cap, 60),
        "behavior": bounded_int(behavior_cap, 15),
    }
    raw = {"verified": 0.0, "semantic": 0.0, "behavior": 0.0}
    for tag, confidence in unique_confidences(tags).items():
        category = TAG_CATEGORIES.get(tag)
        if category is None:
            continue
        weight = max(0.0, finite_number(weights.get(tag), 0.0))
        raw[category] += weight * confidence
    return {
        category: int(min(caps.get(category, 100), value))
        for category, value in raw.items()
    }


def calculate_tag_score(tags: Any, **kwargs) -> int:
    return sum(calculate_tag_components(tags, **kwargs).values())


def risk_level(score: int | None, config: dict) -> str:
    if score is None or isinstance(score, bool):
        return "未知"
    extreme = bounded_int(config.get("risk_level_extreme"), 80)
    high = bounded_int(config.get("risk_level_high"), 60)
    low = bounded_int(config.get("risk_level_low"), 30)
    if score >= extreme:
        return "极高"
    if score >= high:
        return "高"
    if score >= low:
        return "中"
    return "低"


def calculate_mute_components(
    account: dict | None,
    operator_cap: int = 70,
    associated_cap: int = 20,
    resolution_effects: dict | None = None,
    resolution_factor: float = 1.0,
) -> dict[str, int]:
    """Score mute roles independently, with optional bounded resolution offsets.

    Resolution effects are captured when the resolution is recorded.  The factor
    is the remaining fraction: 1 keeps all risk, 0 fully offsets the snapshot.
    Original accounts are never mutated and roles cannot become negative.  Unknown
    legacy aggregate risk remains conservative when no matching snapshot exists.
    """
    account = account if isinstance(account, dict) else {}
    direct_cap = bounded_int(operator_cap, 70)
    indirect_cap = bounded_int(associated_cap, 20)
    schema_version = bounded_int(account.get("risk_schema_version"), 1, 0, 2**31 - 1)
    legacy_total = max(0, int(finite_number(
        account.get("legacy_risk_total", account.get("risk_total") if schema_version < 2 else 0),
        0,
    )))
    if schema_version >= 2:
        legacy_has_operator = bool(account.get("legacy_has_operator"))
    else:
        legacy_has_operator = int(finite_number(account.get("operator_count"), 0)) > 0
    totals = {
        "mute_operator": max(0, int(finite_number(account.get("operator_risk_total"), 0))),
        "mute_associated": max(0, int(finite_number(account.get("associated_risk_total"), 0))),
    }
    totals["mute_operator" if legacy_has_operator else "mute_associated"] += legacy_total
    effects = resolution_effects if isinstance(resolution_effects, dict) else {}
    factor = max(0.0, min(1.0, finite_number(resolution_factor, 1.0)))
    result = {}
    for role, cap in (("mute_operator", direct_cap), ("mute_associated", indirect_cap)):
        resolved = min(
            totals[role], max(0, finite_number(effects.get(role), 0.0)) * (1.0 - factor)
        )
        result[role] = min(cap, max(0, int(totals[role] - resolved)))
    return result
