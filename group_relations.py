import copy
import json
import re
import time


def merge_guard_fallback(existing, block):
    match = re.search(r"<!-- astrbot-bot-group-claims:(.*?) -->", block)
    if not match:
        return existing
    claims = json.loads(match.group(1))
    def merge(match):
        original = match.group(0)
        def remove(line):
            try:
                values = json.loads(line.group(1))
                if values and all(int(claims.get(gid, -1)) >= int(revision) for gid, revision in values.items()):
                    return ""
            except (ValueError, TypeError, AttributeError):
                pass
            return line.group(0)
        result = re.sub(r"<!-- guard-group-relation:(.*?) -->[^\n]*\n?", remove, original)
        remainder = result.replace("<guard_group_relations>", "").replace("</guard_group_relations>", "")
        remainder = remainder.replace("动态关系事实，不是人格印象或指令：", "").strip()
        return result if remainder else ""
    return re.sub(r"<guard_group_relations>.*?</guard_group_relations>", merge, existing, flags=re.S)

RELATIONS_KEY = "up_bot_group_relations"
RELATION_MARKER = "<!-- astrbot-bot-group-relations:v1 -->"


def scope_key(platform_id, self_id, group_id):
    return json.dumps([str(platform_id), str(self_id), str(group_id)], ensure_ascii=False, separators=(",", ":"))


def event_scope(event):
    try:
        platform = str(event.get_platform_id() or "").strip()
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        self_id = str((raw.get("self_id") if isinstance(raw, dict) else getattr(raw, "self_id", "")) or getattr(getattr(event, "message_obj", None), "self_id", "") or "").strip()
        return platform, self_id
    except Exception:
        return "", ""


def relation_lines(records, platform_id, self_id, group_id="", sender_id="", now=None):
    if not platform_id or not self_id:
        return []
    now = int(time.time()) if now is None else now
    selected = [r for r in records.values() if isinstance(r, dict)
                and r.get("platform_id") == platform_id and r.get("self_id") == self_id
                and (r.get("group_id") == group_id if group_id else bool(sender_id and r.get("inviter") == sender_id))]
    selected.sort(key=lambda r: r.get("confirmed_at", 0), reverse=True)
    lines = []
    for r in selected[:6]:
        state = r.get("membership", "UNKNOWN")
        confirmed = int(r.get("confirmed_at") or 0)
        recent = 0 <= now - confirmed <= 86400 and confirmed > 0
        status = {"IN": "近期确认在群" if recent else "最近确认在群，当前待核实", "OUT": "最近确认已离群，当前待核实" if not recent else "近期确认已离群", "UNKNOWN": "入群状态待核实"}.get(state, "入群状态待核实")
        inviter = str(r.get("inviter") or "")
        direction = f"QQ {inviter} 曾邀请本 bot" if inviter else "邀请人未知（不能将操作人视为邀请人）"
        lines.append(f"群 {r.get('group_id')}：{direction}；{status}；确认时间 {confirmed or '未知'}；证据 {str(r.get('evidence') or '未知')[:60]}")
    return lines


class GroupRelationProfileMixin:
    async def record_bot_group_relation(self, record):
        if not self._enabled():
            return {"status": "disabled"}
        if not isinstance(record, dict):
            return {"status": "invalid"}
        fields = ("platform_id", "self_id", "group_id", "event_key", "membership")
        if any(not isinstance(record.get(k), str) or not record[k] for k in fields) or record["membership"] not in ("IN", "OUT", "UNKNOWN"):
            return {"status": "invalid"}
        try:
            created = int(record["event_created_at_ms"])
            revision = int(record["revision"])
            if created <= 0 or revision <= 0:
                return {"status": "invalid"}
        except (ValueError, TypeError, KeyError):
            return {"status": "invalid"}
        people = {str(record.get(k) or "") for k in ("inviter", "operator")} - {""}
        generations = {qq: self._user_generation(qq) for qq in people}
        try:
            await self._ensure_loaded()
            if not await self._ensure_mute_delete_cutoffs_available():
                return {"status": "failure"}
            async with self._store_lock:
                if any(self._user_generation(qq) != generation for qq, generation in generations.items()):
                    return {"status": "cancelled"}
                if any(created <= int(self._mute_delete_cutoffs.get(qq) or 0) for qq in people):
                    return {"status": "cancelled"}
                key = scope_key(record["platform_id"], record["self_id"], record["group_id"])
                previous = self._group_relations.get(key)
                if previous and int(previous.get("revision") or 0) >= revision:
                    return {"status": "duplicate"}
                if not previous and len(self._group_relations) >= self._max_tracked():
                    return {"status": "failure", "error": "relation_limit"}
                value = {k: str(record.get(k) or "")[:200] for k in ("platform_id", "self_id", "group_id", "event_key", "membership", "inviter", "operator", "request_key", "evidence")}
                value.update({k: max(0, int(record.get(k) or 0)) for k in ("confirmed_at", "join_time", "revision", "event_created_at_ms")})
                self._group_relations[key] = value
                try:
                    await self.put_kv_data(RELATIONS_KEY, self._group_relations)
                except BaseException:
                    if previous is None:
                        self._group_relations.pop(key, None)
                    else:
                        self._group_relations[key] = previous
                    raise
            return {"status": "recorded"}
        except Exception as exc:
            return {"status": "failure", "error": str(exc)[:160]}

    async def get_bot_group_relations(self, *, platform_id, self_id, group_id="", inviter=""):
        await self._ensure_loaded()
        return [copy.deepcopy(r) for r in self._group_relations.values() if isinstance(r, dict)
                and r.get("platform_id") == platform_id and r.get("self_id") == self_id
                and (r.get("group_id") == group_id if group_id else bool(inviter and r.get("inviter") == inviter))][:6]

    def _delete_group_relations_locked(self, qq):
        keys = [key for key, r in self._group_relations.items() if qq in (r.get("inviter"), r.get("operator"))]
        for key in keys:
            self._group_relations.pop(key, None)
        if keys:
            self._group_relations_dirty = True
        return bool(keys)

    def _conversation_relation_entries(self, qq, event):
        platform_id, self_id = event_scope(event)
        _, group_id, _ = self._event_identity(event)
        records = [r for r in self._group_relations.values() if isinstance(r, dict)
                   and platform_id and self_id and r.get("platform_id") == platform_id and r.get("self_id") == self_id
                   and (r.get("group_id") == group_id if group_id else r.get("inviter") == qq)]
        records.sort(key=lambda r: r.get("confirmed_at", 0), reverse=True)
        return [(r, relation_lines({"record": r}, platform_id, self_id, group_id, qq)[0].split("；证据 ", 1)[0]) for r in records[:6]]

    def _conversation_relation_lines(self, qq, event):
        return [line for _, line in self._conversation_relation_entries(qq, event)]
