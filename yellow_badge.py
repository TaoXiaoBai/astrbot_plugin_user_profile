"""大黄标的严重违规证据筛选；不读取原话，不执行管理动作。"""
import hashlib
import json

try:
    from .risk_dimensions import sanitize_events
except ImportError:
    from risk_dimensions import sanitize_events


MAJOR_TAGS = frozenset({"scam_suspect"})
_ENFORCEABLE_SEVERITIES = frozenset({"severe", "critical"})


def matched_major_tags(analysis: dict, min_confidence: float) -> list[dict]:
    """返回达到阈值的大黄标标签，输入必须是已清洗的缓存视图。"""
    result = []
    for item in (analysis or {}).get("tags", []):
        if not isinstance(item, dict):
            continue
        tag = item.get("tag")
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if confidence < min_confidence:
            continue
        if tag in MAJOR_TAGS or (tag == "nsfw_tendency" and item.get("severity") == "severe"):
            result.append(item)
    return result


def severe_violations(
    analysis: dict,
    cache_root: dict,
    now: int,
    min_confidence: float,
    window_seconds: int,
) -> list[dict]:
    """只接受与当前大黄标同维度、不同 episode 的严重本人事件。"""
    tags = matched_major_tags(analysis, min_confidence)
    dimensions = set()
    if any(item.get("tag") == "scam_suspect" for item in tags):
        dimensions.add("fraud")
    if any(item.get("tag") == "nsfw_tendency" for item in tags):
        dimensions.add("content_boundary")
    if not dimensions:
        return []

    raw_events = list((analysis or {}).get("risk_events", []))
    root = cache_root if isinstance(cache_root, dict) else {}
    ledger = root.get("risk_evidence") if isinstance(root.get("risk_evidence"), dict) else {}
    scenes = (analysis or {}).get("sources") or [(analysis or {}).get("scene") or "group"]
    for scene in scenes:
        if scene not in ("group", "private"):
            continue
        entry = ledger.get(scene) if isinstance(ledger.get(scene), dict) else {}
        raw_events.extend(entry.get("events") or [])

    episodes = {}
    for item in sanitize_events(raw_events):
        if (
            item["dimension"] not in dimensions
            or item["severity"] not in _ENFORCEABLE_SEVERITIES
            or item["subject"] != "self"
            or item["confidence"] < min_confidence
            or not 0 <= now - item["observed_at"] <= window_seconds
        ):
            continue
        episode = item["episode_id"]
        before = episodes.get(episode)
        if before is None or (item["severity"], item["confidence"], item["observed_at"]) > (
            before["severity"], before["confidence"], before["observed_at"]
        ):
            episodes[episode] = item
    return sorted(episodes.values(), key=lambda item: (item["observed_at"], item["id"]))


def enforcement_event_key(events: list[dict]) -> str:
    """相同证据集合重复评估时复用 QQ Tools 的幂等凭据。"""
    episodes = sorted({str(item.get("episode_id") or "") for item in events})
    encoded = json.dumps(episodes, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return "yellow:" + hashlib.sha256(encoded).hexdigest()[:24]
