"""Pure evidence-based behavioral dimensions; no management ledger or identity scoring."""
import hashlib
import json
import math

DIMENSIONS = frozenset({"content_boundary", "harassment", "fraud"})
SEVERITIES = {"mild": 20, "clear": 60, "severe": 85, "critical": 100}
REVIEW = {"低敏感": 0.85, "中等": 1.0, "高敏感": 1.15}
WINDOW = 30 * 86400
WEIGHTS = {"content_boundary": {"nsfw_tendency": 40},
           "harassment": {"troll": 15, "spam_suspect": 20},
           "fraud": {"scam_suspect": 45, "ad_suspect": 25}}


def material_refs(qq, quotes):
    result = {}
    for q in quotes if isinstance(quotes, list) else []:
        if not isinstance(q, dict) or not isinstance(q.get("text"), str) or not q["text"].strip():
            continue
        timestamp = q.get("t")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp <= 0:
            continue
        material = {"qq": str(qq), "text": q["text"], "observed_at": int(timestamp),
                    "scene": q.get("scene", "unknown"), "group_id": str(q.get("group_id") or "")}
        ref = "r_" + hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        result[ref] = material
    return result


def sanitize_events(raw, materials=None):
    result = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("dimension") not in DIMENSIONS or item.get("severity") not in SEVERITIES or item.get("subject") not in ("self", "third_party", "uncertain"):
            continue
        confidence = item.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            continue
        refs = sorted(set(r for r in item.get("evidence_refs", []) if isinstance(r, str) and r.startswith("r_") and len(r) == 26)) if isinstance(item.get("evidence_refs"), list) else []
        if materials is not None:
            refs = [r for r in refs if r in materials]
        if not refs:
            continue
        requested = item.get("episode_id")
        if isinstance(requested, str) and materials is not None and requested in materials:
            refs = sorted(set(refs + [requested]))
        episode = requested if requested in refs else refs[0]
        connectivity_limited = materials is None and (len(refs) > 64 or item.get("connectivity_limited") is True)
        refs = [episode] + [ref for ref in refs if ref != episode][:(7 if materials is not None else 63)]
        stamp = min(materials[r]["observed_at"] for r in refs) if materials is not None else item.get("observed_at")
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or stamp <= 0:
            continue
        result.append(dict(id=item["dimension"] + ":" + episode, episode_id=episode,
                           dimension=item["dimension"], severity=item["severity"], confidence=float(confidence),
                           subject=item["subject"], evidence_refs=refs, observed_at=int(stamp),
                           connectivity_limited=connectivity_limited))
        if len(result) >= 128:
            break
    return result


def sanitize_concerns(raw):
    result = []
    for item in raw if isinstance(raw, list) else []:
        kind = item.get("kind") if isinstance(item, dict) else None
        if kind in ("dangerous_self_report", "identity_uncertain") and kind not in [i["kind"] for i in result]:
            result.append({"kind": kind, "action": "care_only"})
    return result


def merge_events(previous, current, now):
    unique = {}
    for item in sanitize_events(previous) + sanitize_events(current):
        if not 0 <= now - item["observed_at"] <= WINDOW:
            continue
        key = item["id"]
        before = unique.get(key)
        if before is None:
            unique[key] = item
        else:
            stamp = min(before["observed_at"], item["observed_at"])
            refs = sorted(set(before["evidence_refs"]) | set(item["evidence_refs"]))
            limited = len(refs) > 64 or before["connectivity_limited"] or item["connectivity_limited"]
            if SEVERITIES[item["severity"]] * item["confidence"] > SEVERITIES[before["severity"]] * before["confidence"]:
                unique[key] = item
            unique[key].update(observed_at=stamp, connectivity_limited=limited,
                               evidence_refs=[unique[key]["episode_id"]] + [r for r in refs if r != unique[key]["episode_id"]][:63])
    return sorted(unique.values(), key=lambda item: (item["observed_at"], item["id"]))[-128:]


def score_dimensions(events, now, config=None):
    config = config or {}
    custom = config.get("risk_weights") or {}
    if isinstance(custom, str):
        try:
            custom = json.loads(custom)
        except (ValueError, TypeError):
            custom = {}
    custom = custom if isinstance(custom, dict) else {}
    candidates = []
    normalized = merge_events([], events, now)
    connectivity_limited = any(item["connectivity_limited"] for item in normalized if item["subject"] == "self")
    for item in normalized:
        if item["subject"] != "self":
            continue
        age = now - item["observed_at"]
        recency = max(0.5, min(1.0, 1.0 - age / (2 * WINDOW)))
        dimension = item["dimension"]
        factor = 1.0
        for tag, default in WEIGHTS[dimension].items():
            value = custom.get(tag)
            if isinstance(value, bool):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(number):
                factor = min(factor, max(0.0, number) / default)
        score = SEVERITIES[item["severity"]] * item["confidence"] * recency * REVIEW.get(config.get("risk_review_level"), 1.0) * factor
        candidates.append((score, item))
    # Shared refs form one episode, even when the model labels several dimensions.
    clusters = []
    for score, item in sorted(candidates, key=lambda pair: (-pair[0], pair[1]["id"])):
        refs = set(item["evidence_refs"])
        matches = [c for c in clusters if c["refs"] & refs]
        if matches:
            winner = max(matches + [{"score": score, "item": item}], key=lambda c: c["score"])
            merged_refs = refs.union(*(c["refs"] for c in matches))
            for c in matches:
                clusters.remove(c)
            clusters.append({"score": winner["score"], "item": winner["item"], "refs": merged_refs})
        else:
            clusters.append({"score": score, "item": item, "refs": refs})
    dimensions = {}
    for dimension in sorted(DIMENSIONS):
        rows = [c for c in clusters if c["item"]["dimension"] == dimension and c["score"] > 0]
        # 连续30分钟内的相邻发言最多算一个操作性时段，不把拆句当独立次数。
        stamps = sorted(c["item"]["observed_at"] for c in rows)
        episodes = (1 + sum(b - a > 1800 for a, b in zip(stamps, stamps[1:]))) if stamps else 0
        if connectivity_limited:
            episodes = min(1, episodes)
        persistence = min(1.3, 1.0 + 0.1 * max(0, episodes - 1))
        strongest = max((c["score"] for c in rows), default=0)
        dimensions[dimension] = dict(score=round(min(100, strongest * persistence), 2),
                                     episodes=episodes, persistence=round(persistence, 2),
                                     evidence_refs=sorted(set().union(*(c["refs"] for c in rows))) if rows else [])
    scores = sorted((d["score"] for d in dimensions.values()), reverse=True)
    # 去重连通信息达到容量后，不因丢失连接额外叠加持续性或跨维度分。
    total = min(100, scores[0] + (0 if connectivity_limited else sum(scores[1:]) * 0.25))
    cap = config.get("risk_semantic_cap", 60)
    try:
        cap = max(0, min(100, int(cap)))
    except (ValueError, TypeError, OverflowError):
        cap = 60
    for value in dimensions.values():
        value["score"] = min(cap, value["score"])
    return dict(dimension_score=round(min(cap, total), 2), risk_dimensions=dimensions,
                dimension_status="evidence" if any(scores) else "no_evidence")
