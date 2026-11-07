import math
from copy import deepcopy


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _unique(values):
    return list(dict.fromkeys(values))


def _refs(item):
    values = item.get("evidence_refs", [])
    if not isinstance(values, (list, tuple)):
        return []
    return _unique(value for value in values if isinstance(value, str) and value.startswith("r_"))


def _days(item):
    values = item.get("evidence_days", [])
    if not isinstance(values, (list, tuple)):
        return []
    return _unique(value for value in values if type(value) is int and value >= 0)


def _scenes(item):
    values = item.get("scenes", [])
    if not isinstance(values, (list, tuple)):
        return []
    return _unique(value for value in values if value in ("group", "private"))


def _stability(item):
    days = item["evidence_days"]
    item["stability"] = (
        "established" if item["support_count"] >= 2 and len(days) >= 2
        and max(days) - min(days) >= 1 else "tentative"
    )


def _record(item):
    result = deepcopy(item)
    result["trait"] = item["trait"].strip()
    refs = _refs(item)
    result["evidence_refs"] = refs[-16:]
    result["evidence_days"] = _days(item)[-30:]
    result["scenes"] = _scenes(item)
    result["support_count"] = len(result["evidence_refs"])
    if not refs:
        result["evidence_days"] = []
    _stability(result)
    return result


def _items(values):
    if not isinstance(values, (list, tuple)):
        return
    for item in values:
        if isinstance(item, dict) and isinstance(item.get("trait"), str) and item["trait"].strip():
            yield item


def _valid_refs(item, materials):
    result = []
    for ref in _refs(item):
        quote = materials.get(ref)
        if isinstance(quote, dict) and _positive(quote.get("t")):
            result.append(ref)
    return result


def _evidence_times(item, timestamps):
    times = list(timestamps)
    times.extend(item[key] for key in ("first_evidence_at", "last_evidence_at")
                 if _positive(item.get(key)))
    if times:
        item["first_evidence_at"] = min(times)
        item["last_evidence_at"] = max(times)


def review_long_term_traits(previous, current, retired, materials, now) -> list[dict]:
    materials = materials if isinstance(materials, dict) else {}
    records = {}
    for item in _items(previous):
        key = item["trait"].strip().casefold()
        if key not in records:
            records[key] = _record(item)
            if not _positive(records[key].get("first_seen")):
                records[key]["first_seen"] = now

    for item in _items(current):
        refs = _valid_refs(item, materials)
        if not refs:
            continue
        key = item["trait"].strip().casefold()
        if key in records:
            record = records[key]
        else:
            record = {"trait": item["trait"].strip(), "first_seen": now,
                      "evidence_refs": [], "evidence_days": [], "scenes": [], "support_count": 0}
            records[key] = record
        for field in ("confidence", "reason"):
            if field in item:
                record[field] = deepcopy(item[field])
        old_refs = record["evidence_refs"]
        record["evidence_refs"] = _unique(old_refs + refs)[-16:]
        record["support_count"] = len(record["evidence_refs"])
        times = [materials[ref]["t"] for ref in refs]
        record["evidence_days"] = _unique(record["evidence_days"] + [int(t // 86400) for t in times])[-30:]
        scenes = [materials[ref].get("scene") for ref in refs]
        record["scenes"] = _unique(record["scenes"] + [scene for scene in scenes if scene in ("group", "private")])
        _evidence_times(record, times)
        record["last_confirmed"] = now
        _stability(record)

    for item in _items(retired):
        if _valid_refs(item, materials):
            records.pop(item["trait"].strip().casefold(), None)
    return list(records.values())[:5]


def merge_scene_long_term_traits(group_items, private_items) -> list[dict]:
    records = {}
    for item in list(_items(group_items)) + list(_items(private_items)):
        key = item["trait"].strip().casefold()
        incoming = _record(item)
        if key not in records:
            records[key] = incoming
            continue
        record = records[key]
        refs = _unique(record["evidence_refs"] + _refs(item))
        record["evidence_refs"] = refs[-16:]
        record["support_count"] = len(record["evidence_refs"])
        record["evidence_days"] = _unique(record["evidence_days"] + incoming["evidence_days"])[-30:]
        record["scenes"] = _unique(record["scenes"] + incoming["scenes"])
        confidence = incoming.get("confidence", 0)
        if confidence > record.get("confidence", 0):
            record["confidence"] = deepcopy(confidence)
            record["reason"] = deepcopy(incoming.get("reason", ""))
        first_seen = [value for value in (record.get("first_seen"), incoming.get("first_seen")) if _positive(value)]
        if first_seen:
            record["first_seen"] = min(first_seen)
        confirmed = [value for value in (record.get("last_confirmed"), incoming.get("last_confirmed")) if _positive(value)]
        if confirmed:
            record["last_confirmed"] = max(confirmed)
        _evidence_times(record, [incoming[field] for field in ("first_evidence_at", "last_evidence_at")
                                 if _positive(incoming.get(field))])
        _stability(record)
    return list(records.values())[:5]
