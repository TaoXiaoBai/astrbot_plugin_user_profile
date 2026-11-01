import asyncio
import json
import re
import time

try:
    from .age_review_core import AGE_RELEVANT, UNTRUSTED, age_clues, diagnostics as new_diagnostics, material_ref
    from .profile_core import quote_scene
except ImportError:
    from age_review_core import AGE_RELEVANT, UNTRUSTED, age_clues, diagnostics as new_diagnostics, material_ref
    from profile_core import quote_scene


async def profile_age_material(plugin, qq, event, diagnostics=None):
    diag = diagnostics if diagnostics is not None else new_diagnostics()["profile"]
    weight = plugin.config.get("age_protection_qq_age_weight", 85)
    diag.update(signal_weight=weight)
    if not plugin.config.get("age_protection_use_qq_age", True) or weight <= 0:
        diag["status"] = "disabled"
        return None
    fetch = getattr(plugin, "_fetch_stranger_info", None)
    if not callable(fetch):
        diag["status"] = "unavailable"
        return None
    cache = getattr(plugin, "_age_profile_cache", None)
    if cache is None:
        cache = plugin._age_profile_cache = {}
    key = (id(getattr(event, "bot", None)), qq, plugin._user_generation(qq))
    now = int(time.time())
    entry = cache.get(key)
    ttl = plugin.config.get("age_protection_cache_ttl", 300)
    hit = bool(entry and 0 <= now - entry["time"] < ttl)
    if not hit:
        status, age = "unavailable", None
        try:
            info = await asyncio.wait_for(fetch(qq, event), 3)
            if isinstance(info, dict):
                if "_age_fetch_status" in info:
                    status = info["_age_fetch_status"] if info["_age_fetch_status"] in ("timeout", "error", "unavailable") else "error"
                elif str(info.get("user_id", "")) != qq:
                    status = "identity_mismatch"
                elif info.get("age") is None or info.get("age") == "":
                    status = "missing_age"
                else:
                    raw = info["age"]
                    if isinstance(raw, str) and re.fullmatch(r"[0-9]{1,3}", raw):
                        raw = int(raw)
                    if type(raw) is int and 1 <= raw <= 120:
                        age, status = raw, "ok"
                    else:
                        status = "invalid_age"
        except asyncio.TimeoutError:
            status = "timeout"
        except Exception:
            status = "error"
        if len(cache) >= 5000 and key not in cache:
            cache.pop(next(iter(cache)))
        entry = cache[key] = dict(time=now, age=age, status=status)
    diag.update(status=entry["status"], age=entry["age"], fetched_at=entry["time"], cache_hit=hit)
    if entry["age"] is None:
        return None
    age = entry["age"]
    text = f"QQ资料结构化年龄字段为{age}岁；该字段可由用户自行修改，不是实名年龄验证。"
    return dict(ref=material_ref(qq, f"qq_profile_age:{age}", 0, "profile"), text=text,
                observed_at=entry["time"], scene="platform_profile", source="qq_profile",
                profile_age=age, signal_weight=weight, truncated=False)


def history_tail(encoded, limit):
    if not isinstance(encoded, str) or limit <= 0:
        return [], 0, True
    tail = encoded[-limit:].encode("utf-8")[-limit:].decode("utf-8", errors="ignore")
    used = len(tail.encode("utf-8"))
    truncated = tail != encoded
    candidates = [tail] if not truncated else []
    if truncated:
        for index, match in enumerate(re.finditer(r"(?:^|,)\s*(\{)", tail)):
            if index >= 32:
                break
            candidates.append("[" + tail[match.start(1):])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError, RecursionError):
            continue
        if isinstance(value, list):
            users = [item for item in value if isinstance(item, dict) and item.get("role") == "user"]
            return users[-200:], used, truncated or len(users) > 200
    return [], used, True


def authored_segments(content):
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        parts = [part["text"] for part in content if isinstance(part, dict)
                 and part.get("type") == "text" and isinstance(part.get("text"), str)
                 and part.get("role") in (None, "user")]
    else:
        return []
    result = []
    for part in parts:
        if re.search(r"autoSummary|our previous history|summary|历史摘要|对话总结|聊天总结|会话总结|历史总结|前情提要|记忆摘要", part, re.I):
            continue
        part = re.sub(r"<(system[_-]?reminder|system|quote|blockquote)\b[^>]*>.*?</\1\s*>", "", part, flags=re.I | re.S)
        part = re.sub(r"<(?:system[_-]?reminder|system|quote|blockquote)\b.*$", "", part, flags=re.I | re.S)
        for line in part.splitlines():
            if line.strip() and not re.search(r"系统提醒|\[System|^\s*(?:>|assistant\s*:|助手[：:]|引用[：:])", line, re.I):
                result.append(line)
    return ["\n".join(result)] if result else []


def candidates(text):
    sentences = re.findall(r"[^。！？!?；;\n]+[。！？!?；;\n]*", text)
    spans = []
    for index, sentence in enumerate(sentences):
        if not AGE_RELEVANT.search(sentence):
            continue
        start = index - 1 if index and len(sentences[index - 1]) <= 200 else index
        end = index + 2 if index + 1 < len(sentences) and len(sentences[index + 1]) <= 200 else index + 1
        if spans and start < spans[-1][1]:
            spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
        else:
            spans.append((start, end))
    return ["".join(sentences[a:b]) for a, b in spans]


async def collect_materials(plugin, qq, event, diagnostics=None, *, admin_review=False):
    collection = diagnostics if diagnostics is not None else {}
    _, group, _ = plugin._event_identity(event)
    private_allowed = bool(not group and plugin.config.get("llm_include_private_quotes", True) and plugin.config.get("collect_private", True))
    diag = new_diagnostics(group, private_allowed)
    collection.update(diagnostics=diag, history_truncated=False, material_truncated=False,
                      current_truncated=False, candidate_truncated=False, history_failed=False,
                      undated_adult=False, private_history_included=False)
    if admin_review and not plugin._can_review_age_protection(event):
        return []
    now = int(time.time())
    relevant, ordinary, seen = [], [], set()
    input_budget = 1024 * 1024
    max_chars = plugin.config["age_protection_max_chars"]
    segment_cap = min(8000, max_chars)
    def scan(text, stamp, scene, current=False):
        nonlocal input_budget
        if not isinstance(text, str) or not text:
            return
        size = len(text.encode("utf-8"))
        if size > input_budget:
            collection["current_truncated" if current else "history_truncated"] = True
            text = text.encode("utf-8")[:input_budget].decode("utf-8", errors="ignore")
            size = input_budget
        input_budget -= size
        stamp = plugin._history_record_timestamp(stamp)
        if current and len(text) > segment_cap:
            collection["current_truncated"] = True
        segments = authored_segments(text)
        for segment in segments:
            extracted = candidates(segment)
            if not stamp and any(not c[0] for c in age_clues(segment)):
                collection["undated_adult"] = True
            for value in extracted or ([segment] if len(segment) <= 200 or current else []):
                if value in seen:
                    continue
                seen.add(value)
                relevant_flag = bool(extracted)
                truncated = len(value) > segment_cap
                if truncated and relevant_flag:
                    collection["candidate_truncated"] = True
                if truncated and not current and not relevant_flag:
                    continue
                value = value[:segment_cap]
                item = dict(ref=material_ref(qq, value, stamp, scene), text=value, observed_at=stamp,
                            scene=scene, context_ambiguous=bool(UNTRUSTED.search(segment)),
                            truncated=truncated or (current and collection["current_truncated"]))
                (relevant if relevant_flag else ordinary).append(item)
    if not admin_review:
        try:
            text = event.get_message_str()
        except Exception:
            text = ""
            collection["current_truncated"] = True
        raw = plugin._raw_event(event)
        stamp = plugin._field(raw, "time") or getattr(event, "_profile_age_observed_at", 0) or now
        try:
            event._profile_age_observed_at = stamp
        except Exception:
            pass
        scan(text, stamp, "group" if group else "private", True)
    collection["stage"] = "profile"
    profile = await profile_age_material(plugin, qq, event, diag["profile"])
    collection["stage"] = "history"
    for item in reversed((plugin._quotes or {}).get(qq) or []):
        if not isinstance(item, dict):
            continue
        scene = quote_scene(item)
        if scene == "unknown" or (scene == "private" and not private_allowed):
            continue
        stamp = plugin._history_record_timestamp(item.get("t"))
        retention = plugin.config.get("quote_retention_days", 0)
        if retention and stamp < now - retention * 86400:
            continue
        scan(item.get("text"), stamp, scene)
    h = diag["history"]
    cm = getattr(plugin.context, "conversation_manager", None)
    enabled = plugin.config.get("history_scan_enabled", True)
    if not enabled or not private_allowed:
        h.update(status="disabled", coverage_complete=True)
    elif cm is None:
        collection["history_failed"] = True
    else:
        page_size = min(10, plugin.config["history_scan_page_size"])
        max_pages = min(3, plugin.config["history_scan_pages"])
        parse_budget = 1024 * 1024
        try:
            for page in range(1, max_pages + 1):
                convs, total = await cm.get_filtered_conversations(page=page, page_size=page_size, search_query=qq, include_history=True)
                h["pages_scanned"] += 1
                h["has_more"] = page * page_size < int(total or 0)
                for conv in (convs or [])[:page_size]:
                    origin = str(getattr(conv, "user_id", "") or "")
                    exact = origin == "snowluma:FriendMessage:" + qq
                    group_history = bool(re.fullmatch(r"snowluma:GroupMessage:\d+", origin))
                    if not exact and not group_history:
                        continue
                    h["conversations"] += 1
                    encoded = getattr(conv, "history", "")
                    if not isinstance(encoded, str):
                        collection["history_truncated"] = True
                        continue
                    encoded_size = len(encoded.encode("utf-8"))
                    if encoded_size > parse_budget:
                        collection["history_truncated"] = True
                        continue
                    parse_budget -= encoded_size
                    try:
                        history = json.loads(encoded)
                    except (ValueError, TypeError, RecursionError):
                        collection["history_truncated"] = True
                        continue
                    if not isinstance(history, list):
                        collection["history_truncated"] = True
                        continue
                    for item in history:
                        if not isinstance(item, dict):
                            continue
                        if item.get("role") != "user":
                            h["excluded_messages"] += 1
                            continue
                        sender = str(item.get("sender_id") or "")
                        if (sender and sender != qq) or (not exact and sender != qq):
                            h["excluded_messages"] += 1
                            continue
                        if any(item.get(k) for k in ("summary", "is_summary", "is_system", "synthetic", "tool_calls", "autoSummary")):
                            h["excluded_messages"] += 1
                            continue
                        texts = []
                        for text in authored_segments(item.get("content")):
                            speakers = re.findall(r"\(ID:\s*([^)]*)\)", text)
                            if not speakers or (len(speakers) == 1 and speakers[0].strip() == qq):
                                texts.append(text)
                        if not texts:
                            h["excluded_messages"] += 1
                            continue
                        if h["messages_scanned"] >= 1000 or input_budget <= 0:
                            collection["history_truncated"] = True
                            h["has_more"] = True
                            break
                        h["messages_scanned"] += 1
                        h["owned_messages"] += 1
                        stamp = plugin._history_record_timestamp(item.get("timestamp", item.get("created_at", 0)))
                        h["missing_timestamps"] += not bool(stamp)
                        for text in texts:
                            scan(text, stamp, "private" if exact else "group")
                            collection["private_history_included"] |= exact
                if not convs or not h["has_more"] or input_budget <= 0 or h["messages_scanned"] >= 1000:
                    break
            collection["history_truncated"] |= h["has_more"]
            h.update(status="partial" if collection["history_truncated"] else "ok",
                     coverage_complete=not collection["history_truncated"])
        except asyncio.CancelledError:
            h.update(status="error", coverage_complete=False)
            collection["history_failed"] = True
            raise
        except Exception:
            h.update(status="error", coverage_complete=False)
            collection["history_failed"] = True
            raise
    budget = max_chars
    selected = []
    for item in ([profile] if profile else []) + relevant + ordinary:
        if len(item["text"]) > budget:
            if item in relevant:
                collection["candidate_truncated"] = True
            if not selected and budget > 0:
                item = dict(item, text=item["text"][:budget], truncated=True)
            else:
                continue
        selected.append(item)
        budget -= len(item["text"])
    collection["material_truncated"] = collection["current_truncated"] or collection["candidate_truncated"]
    return selected
