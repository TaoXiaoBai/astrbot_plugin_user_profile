import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone


ALGORITHM_VERSION = 5
REASON_CODES = frozenset({
    "not_started", "unauthorized", "assessment_disabled", "assessment_busy", "assessment_superseded",
    "no_candidate_material", "provider_unavailable", "model_invalid_response", "model_ready",
    "model_unknown", "model_reported_error", "current_message_incomplete", "age_candidates_incomplete",
    "history_coverage_incomplete", "history_unavailable", "undated_adult_counterevidence",
    "identity_or_age_conflict", "grounded_evidence_verified", "no_grounded_evidence", "model_not_eligible",
    "internal_error", "model_quota_exhausted", "model_rate_limited", "model_auth_error",
    "model_context_limit", "model_connection_error", "model_server_error", "model_empty_response",
} | {stage + "_" + outcome for stage in ("load", "queue", "profile", "history", "model", "store")
     for outcome in ("error", "timeout")})
KINDS = frozenset({"self_report_age", "birth_date", "consistent_age_context", "qq_profile_age"})
AGE_RELEVANT = re.compile(r"岁|成年|生日|出生|满\s*(?:18|十八)|换人|账号.{0,4}(?:转让|共用|借用)|不是本人|不是原号主|换了号主")
IDENTITY = re.compile(r"换人|账号(?:转让|共用|借用)|不是本人|不是原号主|换了号主")
UNTRUSTED = re.compile(r'[“”「」『』"<>]|引用|转发|他说|她说|朋友|同学|弟弟|妹妹|姐姐|哥哥|孩子|女儿|儿子|扮演|假设|比如|小说|角色|系统提醒|summary|system reminder|开玩笑|骗你的|谎称|不是我|并非|不是真的|如果|假如|也许|可能|大概|估计|虚构|岁[吗?？]', re.I)


def diagnostics(group=False, private_allowed=False):
    return dict(
        profile=dict(status="unavailable", age=None, signal_weight=0,
                     source="onebot_get_stranger_info", fetched_at=0, cache_hit=False),
        history=dict(status="unavailable", conversations=0, messages_scanned=0, owned_messages=0,
                     excluded_messages=0, missing_timestamps=0, pages_scanned=0, has_more=False,
                     coverage_complete=False),
        evidence_summary=dict(self_minor=0, self_adult=0, profile_minor=0, profile_adult=0,
                              ambiguous=0, identity_conflicts=0, selected=0),
        model=dict(status="not_called", reason_code="not_started"),
        decision=dict(status="unavailable", reasons=["not_started"]),
        scope=dict(scene="group" if group else "private", private_history_allowed=private_allowed))


def material_ref(qq, text, observed_at, scene):
    value = json.dumps([qq, text, observed_at, scene], ensure_ascii=False, separators=(",", ":"))
    return "m_" + hashlib.sha256(value.encode()).hexdigest()[:24]


def chinese_number(value):
    if value.isascii() and value.isdigit():
        return int(value)
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if "十" in value:
        parts = value.split("十")
        if len(parts) == 2 and all(len(p) <= 1 for p in parts):
            return digits.get(parts[0], 1) * 10 + digits.get(parts[1], 0)
    if len(value) == 1:
        return digits.get(value)
    return None


def age_clues(text):
    facts = []
    for sentence in re.split(r"[。！？!?；;\n]", text):
        if UNTRUSTED.search(sentence):
            continue
        if re.search(r"曾经|以前|当时|那年|小时候|年前|岁(?:的?时候|时|以上|以下|左右)", sentence):
            continue
        for match in re.finditer(r"(?:我(?:今年|现在|目前)?(?:已经|才|刚满|已满|刚)?|^\s*今年)\s*([0-9零一二三四五六七八九十两]{1,3})\s*(?:周岁|岁)", sentence):
            age = chinese_number(match.group(1))
            if age is not None and 0 <= age <= 120:
                facts.append((age < 18, "self_report_age", age))
        if re.search(r"我(?:今年|现在|今天)?(?:已经|已|刚)?成年(?:了)?|我(?:今年|现在|今天)?(?:已经|刚|已)?满\s*(?:18|十八)(?:周?岁)?(?:了)?|我.{0,4}生日.{0,4}(?:满|过了)\s*(?:18|十八)|我(?:今天|今年)?(?:过|刚过)?(?:18|十八)(?:周?岁)?生日|我(?:的)?(?:18|十八)(?:周?岁)?生日(?:已经|刚|才)?(?:过了|到了|刚过)", sentence):
            facts.append((False, "self_report_age", 18))
        if re.search(r"我(?:今年|现在|还)?(?:没有|没|未)成年|我(?:今年|现在|还)?(?:未满|没满)\s*(?:18|十八)", sentence):
            facts.append((True, "self_report_age", None))
    return facts


def hard_age(text, observed_at, now):
    if UNTRUSTED.search(text):
        return None
    born = re.search(r"我(?:的生日是|出生于|是)?\s*((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)", text)
    if born:
        try:
            birth = datetime(*map(int, born.groups()), tzinfo=timezone.utc)
            today = datetime.fromtimestamp(now, timezone.utc)
            age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
            return (age < 18, "birth_date") if 0 <= age <= 120 else None
        except ValueError:
            return None
    clues = age_clues(text)
    if not clues or len({c[0] for c in clues}) > 1 or not observed_at or observed_at > now + 300:
        return None
    minor, kind, age = clues[0]
    elapsed = max(0, now - observed_at) / (365.2425 * 86400)
    same_day = datetime.fromtimestamp(observed_at, timezone.utc).date() == datetime.fromtimestamp(now, timezone.utc).date()
    if minor and not same_day and (age is None or age + math.ceil(elapsed) >= 18):
        return None
    return minor, kind


def material_age_fact(material, now):
    if material.get("truncated") or material.get("context_ambiguous"):
        return None
    if material.get("source") == "qq_profile":
        age = material.get("profile_age")
        return (age < 18, "qq_profile_age") if type(age) is int and 1 <= age <= 120 else None
    return hard_age(material["text"], material["observed_at"], now)


def unknown(status="unknown", reason="资料不足", fingerprint=""):
    return dict(status=status, suspected_minor=False, minor_confidence=0.0,
                decision_eligible=False, identity_conflict=False, evidence=[],
                reason=reason, assessed_at=int(time.time()), fingerprint=fingerprint)


def valid_response(raw):
    if not isinstance(raw, dict) or raw.get("status") not in ("ready", "unknown", "error"):
        return False
    confidence = raw.get("minor_confidence")
    return (type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1
            and all(type(raw.get(k)) is bool for k in ("suspected_minor", "decision_eligible", "identity_conflict"))
            and isinstance(raw.get("evidence"), list))


def sanitize(raw, materials, fingerprint, now):
    result = unknown(fingerprint=fingerprint)
    if not valid_response(raw):
        return result
    known = {m["ref"]: material_age_fact(m, now) for m in materials}
    evidence = []
    for item in raw["evidence"]:
        if (not isinstance(item, dict) or not isinstance(item.get("kind"), str)
                or not isinstance(item.get("ref"), str) or item["kind"] not in KINDS
                or type(item.get("supports_minor")) is not bool):
            continue
        fact = known.get(item.get("ref"))
        if fact is None or fact[0] != item["supports_minor"] or (item["kind"] != fact[1] and item["kind"] != "consistent_age_context"):
            continue
        if any(e["ref"] == item["ref"] for e in evidence):
            continue
        evidence.append(dict(ref=item["ref"], kind=fact[1], supports_minor=fact[0],
                             reason="QQ资料年龄线索（非实名验证）" if fact[1] == "qq_profile_age" else "本人明确年龄材料"))
    facts = [value[0] for value in known.values() if value is not None]
    soft = [clue[0] for m in materials if m.get("source") != "qq_profile" for clue in age_clues(m["text"])]
    conflict = (raw["identity_conflict"] or len(set(facts + soft)) > 1
                or any(IDENTITY.search(m["text"]) for m in materials))
    suspected = raw["suspected_minor"]
    eligible = (raw["status"] == "ready" and raw["decision_eligible"] and not conflict
                and not any(m.get("truncated") for m in materials)
                and any(item["supports_minor"] == suspected for item in evidence)
                and (not suspected or False not in facts + soft))
    result.update(status="unknown" if raw["status"] == "ready" and not evidence else raw["status"],
                  suspected_minor=suspected if evidence else False,
                  minor_confidence=float(raw["minor_confidence"]) if evidence else 0.0,
                  decision_eligible=bool(eligible), identity_conflict=bool(conflict), evidence=evidence[:12],
                  reason="存在身份或年龄矛盾" if conflict else ("综合材料评估" if evidence else "缺少本人明确年龄材料"))
    return result


def summarize(materials, now):
    counts = diagnostics()["evidence_summary"]
    counts["selected"] = len(materials)
    signals = set()
    for m in materials:
        counts["identity_conflicts"] += bool(IDENTITY.search(m["text"]))
        fact = material_age_fact(m, now)
        if fact:
            signals.add(fact[0])
            prefix = "profile" if m.get("source") == "qq_profile" else "self"
            counts[prefix + ("_minor" if fact[0] else "_adult")] += 1
        elif AGE_RELEVANT.search(m["text"]):
            counts["ambiguous"] += 1
        if m.get("source") != "qq_profile":
            signals.update(clue[0] for clue in age_clues(m["text"]))
    counts["identity_conflicts"] += len(signals) > 1
    return counts


def finish_decision(result, diag, collection):
    reasons = []
    if collection.get("current_truncated"):
        reasons.append("current_message_incomplete")
    if collection.get("candidate_truncated"):
        reasons.append("age_candidates_incomplete")
    if collection.get("history_truncated"):
        reasons.append("history_coverage_incomplete")
    if collection.get("history_failed"):
        reasons.append("history_unavailable")
    if collection.get("undated_adult"):
        reasons.append("undated_adult_counterevidence")
    if reasons:
        result["decision_eligible"] = False
        result["reason"] += "（上下文截断或未核实，不可据此自动限制）"
    if result["identity_conflict"]:
        status = "conflict"
        reasons.append("identity_or_age_conflict")
    elif reasons:
        status = "incomplete_coverage"
    elif result["decision_eligible"]:
        status = "eligible_minor" if result["suspected_minor"] else "adult_or_not_minor"
        reasons.append("grounded_evidence_verified")
    elif diag["model"]["status"] in ("error", "timeout", "unavailable", "invalid_response"):
        status = "unavailable"
        reasons.append(diag["model"]["reason_code"])
    else:
        status = "insufficient_evidence"
        reasons.append("no_grounded_evidence" if not result["evidence"] else "model_not_eligible")
    diag["decision"] = dict(status=status, reasons=reasons)
    result["diagnostics"] = diag
    return result
