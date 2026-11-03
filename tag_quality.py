"""标签质量规则；只校验已选材料与结构，不替代模型理解语境。"""
import hashlib
import json
import math
import re
import unicodedata

try:
    from .tag_catalog import TAG_CATALOG, SEMANTIC_RISK_TAGS
except ImportError:
    from tag_catalog import TAG_CATALOG, SEMANTIC_RISK_TAGS

TAG_REF_PATTERN = re.compile(r"^[rt]_[a-f0-9]{24}$")
DESCRIPTIVE_MIN_CONFIDENCE = 0.6

# 只在依据被具体标签完全覆盖且可靠性不更低时去掉泛化描述。
SPECIFIC_TAGS = {
    "tech_interest": {"coding_interest", "ai_interest", "hardware_interest",
                      "open_source_interest", "automation_interest"},
    "creative_interest": {"design_interest", "handcraft_interest"},
    "helpful": {"patient_explainer", "public_qa", "knowledge_sharing"},
    "summary_expression": {"discussion_recap"},
    "resource_sharing": {"group_resource_curation"},
}


def quote_tag_ref(qq: str, quote: dict, timed_refs: dict) -> str:
    if timed_refs:
        return next(iter(timed_refs))
    # 缺时间也能描述沟通行为，但绝不制造日期或变成可计时的风险事件。
    material = {"qq": str(qq), "text": quote.get("text", ""),
                "scene": quote.get("scene", "unknown"),
                "group_id": str(quote.get("group_id") or "")}
    encoded = json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "t_" + hashlib.sha256(encoded).hexdigest()[:24]


def safe_tag_refs(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    return sorted({ref for ref in raw if isinstance(ref, str) and TAG_REF_PATTERN.fullmatch(ref)})[:8]


def tag_strength(item: dict) -> float:
    value = item.get("confidence", 0)
    if isinstance(value, bool):
        return 0.0
    try:
        confidence = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(confidence):
        return 0.0
    factor = {"mild": 0.5, "clear": 1.0, "severe": 1.5}.get(str(item.get("severity") or "clear"), 1.0) if item.get("tag") == "nsfw_tendency" else 1.0
    return max(0.0, min(1.0, confidence)) * factor


def consolidate_tags(items: list[dict]) -> list[dict]:
    unique = {}
    for item in items:
        tag = item["tag"]
        if tag not in unique or tag_strength(item) > tag_strength(unique[tag]):
            unique[tag] = item
    if len(unique) > 1:
        unique.pop("normal", None)
    for parent, children in SPECIFIC_TAGS.items():
        item = unique.get(parent)
        refs = set(safe_tag_refs(item.get("evidence_refs"))) if item else set()
        if not refs:
            continue
        covered = set()
        for child in children:
            specific = unique.get(child)
            if specific and specific["confidence"] >= item["confidence"]:
                covered.update(safe_tag_refs(specific.get("evidence_refs")))
        if refs <= covered:
            unique.pop(parent, None)
    return list(unique.values())


def ground_generated_tags(raw, materials: dict) -> list[dict]:
    """新模型结果必须引用当前实际送入的本人候选；旧缓存走兼容清洗。"""
    result = []
    for item in raw[:240] if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("tag"), str) or item["tag"] not in TAG_CATALOG:
            continue
        confidence = item.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            continue
        try:
            confidence = float(confidence)
        except (ValueError, OverflowError):
            continue
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            continue
        reason = item.get("reason")
        if item.get("subject") != "self" or not isinstance(reason, str):
            continue
        reason = " ".join("".join(ch for ch in reason if not unicodedata.category(ch).startswith("C")).split())[:160]
        if not reason:
            continue
        requested = item.get("evidence_refs")
        if not isinstance(requested, list) or not requested or len(requested) > 8:
            continue
        if any(not isinstance(ref, str) or ref not in materials for ref in requested):
            continue
        refs = safe_tag_refs(requested)
        if not refs:
            continue
        tag = item["tag"]
        if tag not in SEMANTIC_RISK_TAGS and confidence < DESCRIPTIVE_MIN_CONFIDENCE:
            continue
        support = item.get("support_kind", "observed_behavior")
        if support not in ("explicit_preference", "observed_behavior"):
            continue
        if TAG_CATALOG[tag][1] == "兴趣话题" and support != "explicit_preference":
            # 标点、空白、大小写或全半角变体不算新的独立讨论。
            texts = {evidence_text_key(materials[ref]["text"]) for ref in refs}
            texts.discard("")
            if len(texts) < 2:
                continue
        result.append(dict(item, confidence=confidence, reason=reason, evidence_refs=refs, support_kind=support))
    return result


def evidence_text_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in normalized
                   if not ch.isspace() and unicodedata.category(ch)[0] not in ("P", "C"))


def validate_generated_tags(raw: list, materials: dict) -> list[dict]:
    """结构或风险证据不完整时整次失败，避免正向有效项掩盖错误风险项。"""
    if len(raw) > 240:
        raise ValueError("profile_tags_exceed_candidate_budget")
    result = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("tag"), str) or item["tag"] not in TAG_CATALOG:
            raise ValueError("profile_unknown_or_malformed_tag")
        accepted = ground_generated_tags([item], materials)
        if item["tag"] in SEMANTIC_RISK_TAGS and not accepted:
            raise ValueError("profile_risk_tag_without_grounded_evidence")
        result.extend(accepted)
    return result
